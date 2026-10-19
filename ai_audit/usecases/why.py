"""
ユースケースB v2: 設計思想（Why）の抽出・蓄積・検索

保存先: <対象>/.ai_audit/why.db（SQLite、プロジェクトごと）
  why(id, file, qualname, kind, line, content_hash, text, extracted_at, model, embedding)
VSCode 拡張向けに <対象>/.ai_audit/why.json（ファイル → エントリ一覧）も書き出す。

検索:
  - LLM_EMBEDDING_MODEL が設定されていれば、推論サーバの /v1/embeddings でベクトル検索
  - 併せて文字 2-gram による語彙検索を行い、両者を合成する（埋め込みなしでも日本語で検索できる）
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

import requests

from ..context.builder import number_lines
from ..core.config import Settings, load_settings
from ..core.runtime import Runtime, create_runtime
from ..index.graph import CodeGraph
from ..pipeline.engine import Pipeline, StepFailed
from ..pipeline.jobstore import JobStore, atomic_write, work_base
from ..pipeline.llm_steps import new_builder, text_step
from ..wears import why as WW

_KINDS = ("function", "method", "class")


def _db_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), ".ai_audit", "why.db")


def _connect(root: str) -> sqlite3.Connection:
    path = _db_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS why (
        id TEXT PRIMARY KEY, file TEXT, qualname TEXT, kind TEXT, line INTEGER,
        content_hash TEXT, text TEXT, extracted_at TEXT, model TEXT, embedding TEXT)""")
    return conn


# ---------------------------------------------------------------------------
# 埋め込み
# ---------------------------------------------------------------------------
def embed(settings: Settings, texts: list[str]) -> list[list[float]] | None:
    """推論サーバの /v1/embeddings でベクトル化する（未設定・失敗時は None）。"""
    if not settings.embedding_model or not texts:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    vectors = []
    try:
        for i in range(0, len(texts), 16):
            r = requests.post(f"{settings.api_base_url.rstrip('/')}/embeddings", headers=headers,
                              json={"model": settings.embedding_model, "input": texts[i:i + 16]}, timeout=300)
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            vectors += [d["embedding"] for d in data]
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[WARN] 埋め込みの取得に失敗しました（語彙検索のみ使用）: {e}", file=sys.stderr)
        return None
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _grams(text: str) -> Counter:
    text = re.sub(r"\s+", " ", text.lower())
    words = re.findall(r"[a-z0-9_]{2,}", text)
    jp = re.sub(r"[a-z0-9_\s\W]", "", text)
    return Counter(words + [jp[i:i + 2] for i in range(len(jp) - 1)])


# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------
def extract_why(directory: str, force: bool = False, runtime: Runtime | None = None,
                quiet: bool = False) -> dict:
    """シンボルごとに設計思想を抽出して保存する。変更のないシンボルはスキップする。"""
    abs_dir = os.path.abspath(directory)
    rt = runtime or create_runtime(quiet=quiet)
    graph = CodeGraph(abs_dir)
    graph.update()
    symbols = [s for s in graph.symbols.values() if s.kind in _KINDS]
    conn = _connect(abs_dir)
    stored = {r["id"]: r["content_hash"] for r in conn.execute("SELECT id, content_hash FROM why")}
    pipe = Pipeline("why", JobStore(work_base(abs_dir), "why"), rt, force=force,
                    version=WW.PROMPT_VERSION, quiet=quiet)

    def code_of(sym):
        return graph.symbol_source(sym.id)

    def chash(sym):
        return hashlib.sha256(f"{WW.PROMPT_VERSION}|{rt.settings.model_name}|{code_of(sym)}".encode()).hexdigest()[:16]

    targets = [s for s in symbols if force or stored.get(s.id) != chash(s)]
    pipe.stage("設計思想の抽出", len(targets))

    def run(sym):
        b = new_builder(rt, WW.WHY_EXTRACTOR)
        source = number_lines(code_of(sym), sym.start_line)
        b.add("target", f"{sym.id}（{sym.lang}）", [source, f"{sym.signature}\n{sym.doc}"], required=True)
        if sym.parent and sym.parent in graph.symbols:
            parent = graph.symbols[sym.parent]
            b.add("parent", "所属クラス", f"{parent.signature}\n{parent.doc}", priority=20)
        callers = graph.callers(sym.id)
        if callers:
            b.add("callers", "呼び出し元", "\n".join(callers[:10]), priority=30)
        return text_step(pipe, rt, f"why/{sym.id}", WW.WHY_EXTRACTOR, b)["result"]

    texts = pipe.map(targets, name_fn=lambda s: f"why-sym/{s.id}",
                     inputs_fn=lambda s: {"hash": chash(s)}, fn=run, label_fn=lambda s: s.id)
    done = [(s, t) for s, t in zip(targets, texts) if t]
    vectors = embed(rt.settings, [f"{s.qualname}\n{t}" for s, t in done]) or [None] * len(done)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for (s, t), vec in zip(done, vectors):
            conn.execute("INSERT OR REPLACE INTO why VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (s.id, s.file, s.qualname, s.kind, s.start_line, chash(s), t, now,
                          rt.settings.model_name, json.dumps(vec) if vec else None))
        # 削除されたシンボルを除去
        live = {s.id for s in symbols}
        for sid in set(stored) - live:
            conn.execute("DELETE FROM why WHERE id = ?", (sid,))
    export_json(abs_dir, conn)
    conn.close()
    pipe.finish()
    rt.close()
    stats = {"extracted": len(done), "skipped": len(symbols) - len(targets),
             "failed": len(targets) - len(done), "removed": len(set(stored) - {s.id for s in symbols})}
    if not quiet:
        print(f"[INFO] 設計思想: 抽出 {stats['extracted']} / 変更なし {stats['skipped']} / "
              f"失敗 {stats['failed']} / 削除 {stats['removed']}")
    return stats


def export_json(root: str, conn: sqlite3.Connection | None = None) -> str:
    """VSCode 拡張用に <root>/.ai_audit/why.json を書き出す。"""
    own = conn is None
    conn = conn or _connect(root)
    data: dict[str, list] = {}
    for r in conn.execute("SELECT * FROM why ORDER BY file, line"):
        data.setdefault(r["file"], []).append({
            "id": r["id"], "name": r["qualname"].split(".")[-1], "qualname": r["qualname"],
            "kind": r["kind"], "line": r["line"], "why": r["text"], "extracted_at": r["extracted_at"]})
    if own:
        conn.close()
    path = os.path.join(os.path.abspath(root), ".ai_audit", "why.json")
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))
    return path


# ---------------------------------------------------------------------------
# 検索・一覧
# ---------------------------------------------------------------------------
def _rows(root: str) -> list[sqlite3.Row]:
    if not os.path.exists(_db_path(root)):
        return []
    conn = _connect(root)
    rows = conn.execute("SELECT * FROM why ORDER BY file, line").fetchall()
    conn.close()
    return rows


def search_why(query: str, directory: str = ".", top_k: int = 5, settings: Settings | None = None) -> list[dict]:
    rows = _rows(directory)
    if not rows:
        return []
    settings = settings or load_settings()
    docs = [_grams(f"{r['qualname']} {r['text']}") for r in rows]
    df = Counter(g for d in docs for g in d)
    n = len(docs)
    q = _grams(query)
    lexical = []
    for d in docs:
        score = sum(math.log(1 + n / df[g]) * min(c, d[g]) for g, c in q.items() if g in d)
        lexical.append(score)
    max_lex = max(lexical) or 1.0
    qvec = None
    if any(r["embedding"] for r in rows):
        vecs = embed(settings, [query])
        qvec = vecs[0] if vecs else None
    results = []
    for r, lex in zip(rows, lexical):
        score = lex / max_lex
        if qvec and r["embedding"]:
            score = 0.7 * _cosine(qvec, json.loads(r["embedding"])) + 0.3 * score
        results.append((score, r))
    results.sort(key=lambda x: -x[0])
    return [{"rank": i + 1, "file_path": r["file"], "function_name": r["qualname"], "chunk_type": r["kind"],
             "line": r["line"], "why_text": r["text"], "score": round(s, 4)}
            for i, (s, r) in enumerate(results[:top_k]) if s > 0]


def list_why(directory: str = ".") -> list[dict]:
    return [{"index": i + 1, "file_path": r["file"], "function_name": r["qualname"], "chunk_type": r["kind"],
             "line": r["line"], "why_text": r["text"], "extracted_at": r["extracted_at"]}
            for i, r in enumerate(_rows(directory))]


def print_list_results(results: list[dict]) -> None:
    if not results:
        print("蓄積された設計思想がありません。まず extract_why を実行してください。")
        return
    print(f"\n[INFO] 蓄積済み設計思想: {len(results)} 件")
    for item in results:
        print(f"\n--- #{item['index']} [{item['chunk_type']}] {item['function_name']} ---")
        print(f"ファイル   : {item['file_path']}:{item['line']}")
        print(f"抽出日時   : {item['extracted_at']}")
        print(f"設計思想   :\n{item['why_text']}")


def print_search_results(results: list[dict]) -> None:
    if not results:
        print("関連する設計思想が見つかりませんでした。")
        return
    for item in results:
        print(f"\n--- #{item['rank']} [{item['chunk_type']}] {item['function_name']} ---")
        print(f"ファイル : {item['file_path']}:{item['line']}")
        print(f"スコア   : {item['score']}")
        print(f"設計思想 :\n{item['why_text']}")
