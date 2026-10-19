"""
Code Graph: リポジトリの静的解析結果（ファイル・シンボル・依存・呼び出し）

保存: <root>/.ai_audit/graph.db（SQLite）
  - files テーブルにファイル単位の解析結果（ParsedFile）を JSON で保存する
  - ハッシュが変わったファイルだけ再解析する（差分更新）
  - import・呼び出しの解決はグラフ読み込み時にメモリ上で行う

モジュール = ファイルが置かれたディレクトリ（ルート直下のファイルは "(root)"）
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from .model import Call, ImportRef, ParsedFile, Symbol
from .parsers import parse_source
from .scanner import read_source, scan_source_files, to_rel

PARSER_VERSION = "3"  # パーサ・保存形式を変えたら上げる（全ファイル再解析になる）
ROOT_MODULE = "(root)"
_JS_RESOLVE_EXT = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]


def _to_parsed(data: dict) -> ParsedFile:
    syms = []
    for s in data.pop("symbols", []):
        s["calls"] = [Call(**c) for c in s.get("calls", [])]
        syms.append(Symbol(**s))
    imports = [ImportRef(**i) for i in data.pop("imports", [])]
    return ParsedFile(symbols=syms, imports=imports, **data)


def module_of(path: str) -> str:
    d = os.path.dirname(path)
    return d if d else ROOT_MODULE


class CodeGraph:
    def __init__(self, root: str, db_path: str | None = None):
        self.root = os.path.abspath(root)
        self.db_path = db_path or os.path.join(self.root, ".ai_audit", "graph.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.files: dict[str, ParsedFile] = {}
        self._init_db()

    # ------------------------------------------------------------------
    # 永続化
    # ------------------------------------------------------------------
    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, lang TEXT, data TEXT NOT NULL,
                    mtime REAL, size INTEGER
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """)
            row = conn.execute("SELECT value FROM meta WHERE key='parser_version'").fetchone()
            if not row or row[0] != PARSER_VERSION:
                conn.execute("DROP TABLE files")
                conn.execute("""CREATE TABLE files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                                lang TEXT, data TEXT NOT NULL, mtime REAL, size INTEGER)""")
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('parser_version', ?)", (PARSER_VERSION,))

    def update(self, progress=None) -> dict:
        """
        ファイルを走査して変更分だけ再解析し、グラフを読み込む。
        Returns: {"added": n, "changed": n, "removed": n, "unchanged": n}
        """
        stats = {"added": 0, "changed": 0, "removed": 0, "unchanged": 0}
        with self._connect() as conn:
            stored = {p: (sha, mt, sz) for p, sha, mt, sz in
                      conn.execute("SELECT path, sha256, mtime, size FROM files").fetchall()}
            seen = set()
            paths = list(scan_source_files(self.root))
            for i, abs_path in enumerate(paths, 1):
                rel = to_rel(abs_path, self.root)
                seen.add(rel)
                st = os.stat(abs_path)
                old = stored.get(rel)
                if old and old[1] == st.st_mtime and old[2] == st.st_size:
                    stats["unchanged"] += 1   # 更新日時・サイズが同じなら読み込みも省略
                    continue
                source = read_source(abs_path)
                sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
                if old and old[0] == sha:
                    conn.execute("UPDATE files SET mtime = ?, size = ? WHERE path = ?",
                                 (st.st_mtime, st.st_size, rel))
                    stats["unchanged"] += 1
                    continue
                pf = parse_source(rel, source)
                conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                             (rel, pf.sha256, pf.lang, json.dumps(asdict(pf), ensure_ascii=False),
                              st.st_mtime, st.st_size))
                stats["changed" if rel in stored else "added"] += 1
                if progress:
                    progress(i, len(paths), rel)
            for rel in set(stored) - seen:
                conn.execute("DELETE FROM files WHERE path = ?", (rel,))
                stats["removed"] += 1
        self.load()
        return stats

    def load(self) -> None:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM files ORDER BY path").fetchall()
        self.files = {}
        self._source_cache: dict[str, str] = {}
        for (data,) in rows:
            pf = _to_parsed(json.loads(data))
            self.files[pf.path] = pf
        self._build()

    # ------------------------------------------------------------------
    # グラフ構築（メモリ上）
    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.symbols: dict[str, Symbol] = {}
        self._by_name: dict[str, list[Symbol]] = defaultdict(list)
        for pf in self.files.values():
            for s in pf.symbols:
                self.symbols[s.id] = s
                self._by_name[s.name].append(s)
        self._py_modules = self._python_module_map()
        self.file_deps: dict[str, set[str]] = {}
        self.external_deps: dict[str, set[str]] = {}
        for path, pf in self.files.items():
            internal, external = set(), set()
            for imp in pf.imports:
                target = self.resolve_import(path, imp)
                if target and target != path:
                    internal.add(target)
                elif not target:
                    ext = self._external_name(pf.lang, imp)
                    if ext:
                        external.add(ext)
            self.file_deps[path] = internal
            self.external_deps[path] = external
        self._dependents: dict[str, set[str]] = defaultdict(set)
        for src, targets in self.file_deps.items():
            for dst in targets:
                self._dependents[dst].add(src)
        self._modules: dict[str, list[str]] = defaultdict(list)
        for path in self.files:
            self._modules[module_of(path)].append(path)
        self._modules = dict(sorted(self._modules.items()))
        self._resolve_calls()

    def _python_module_map(self) -> dict[str, str]:
        mapping = {}
        for path, pf in self.files.items():
            if pf.lang != "python":
                continue
            mod = path[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mapping[mod] = path
        return mapping

    def _resolve_python(self, path: str, imp: ImportRef) -> str | None:
        if imp.level:
            base = path.split("/")[:-1]
            if imp.level > 1:
                base = base[: len(base) - (imp.level - 1)]
            parts = base + ([p for p in imp.module.split(".") if p] if imp.module else [])
            candidates = [".".join(parts + [n]) for n in imp.names] + [".".join(parts)]
        else:
            candidates = [f"{imp.module}.{n}" for n in imp.names] + [imp.module]
        for cand in candidates:
            if cand in self._py_modules:
                return self._py_modules[cand]
            # src/ レイアウト等: 末尾一致で探す
            matches = [p for m, p in self._py_modules.items() if m.endswith("." + cand)]
            if len(matches) == 1:
                return matches[0]
        return None

    def _resolve_relative_file(self, path: str, spec: str, exts: list[str]) -> str | None:
        base = os.path.normpath(os.path.join(os.path.dirname(path), spec)).replace(os.sep, "/")
        options = [base] + [base + e for e in exts] + [f"{base}/index{e}" for e in exts]
        # TypeScript では .js 指定で .ts を指すことがある
        stem, ext = os.path.splitext(base)
        if ext in (".js", ".jsx", ".mjs"):
            options += [stem + e for e in (".ts", ".tsx")]
        return next((o for o in options if o in self.files), None)

    def resolve_import(self, path: str, imp: ImportRef) -> str | None:
        """import を解決し、リポジトリ内のファイルパスを返す（外部なら None）。"""
        lang = self.files[path].lang
        if lang == "python":
            return self._resolve_python(path, imp)
        if lang in ("javascript", "typescript"):
            if imp.module.startswith("."):
                return self._resolve_relative_file(path, imp.module, _JS_RESOLVE_EXT)
            return None
        if lang == "dart":
            if imp.module.startswith("package:"):
                rest = imp.module.split("/", 1)[1] if "/" in imp.module else ""
                matches = [p for p in self.files if p.endswith("lib/" + rest)]
                return matches[0] if len(matches) == 1 else None
            if imp.module.startswith("dart:"):
                return None
            return self._resolve_relative_file(path, imp.module, [])
        return None

    @staticmethod
    def _external_name(lang: str, imp: ImportRef) -> str:
        if lang == "python":
            top = imp.module.split(".")[0]
            # 標準ライブラリは外部依存として扱わない
            return "" if imp.level or top in sys.stdlib_module_names else top
        if lang in ("javascript", "typescript"):
            if imp.module.startswith("."):
                return ""
            parts = imp.module.split("/")
            return "/".join(parts[:2]) if imp.module.startswith("@") else parts[0]
        if lang == "dart":
            if imp.module.startswith("dart:"):
                return imp.module
            if imp.module.startswith("package:"):
                return imp.module[len("package:"):].split("/")[0]
        return ""

    def _resolve_calls(self) -> None:
        """呼び出し名を、同一クラス → 同一ファイル → import 先 → 全体で一意 の順で解決する。"""
        self.call_edges: list[tuple[str, str, int, str]] = []   # (caller, callee, line, confidence)
        self.unresolved_calls: dict[str, list[str]] = defaultdict(list)
        for sym in self.symbols.values():
            if not sym.calls:
                continue
            deps = self.file_deps.get(sym.file, set())
            for call in sym.calls:
                cands = [c for c in self._by_name.get(call.simple_name, []) if c.id != sym.id]
                if not cands:
                    self.unresolved_calls[sym.id].append(call.name)
                    continue
                chosen, conf = None, "ambiguous"
                if call.name.startswith(("self.", "this.")) and sym.parent:
                    same_cls = [c for c in cands if c.parent == sym.parent]
                    if same_cls:
                        chosen, conf = same_cls[0], "resolved"
                if not chosen:
                    for scope in ([c for c in cands if c.file == sym.file],
                                  [c for c in cands if c.file in deps],
                                  cands):
                        if len(scope) == 1:
                            chosen, conf = scope[0], "resolved"
                            break
                        if scope and not chosen:
                            chosen = scope[0]
                            break
                self.call_edges.append((sym.id, chosen.id, call.line, conf))
        self._callees: dict[str, set[str]] = defaultdict(set)
        self._callers: dict[str, set[str]] = defaultdict(set)
        for src, dst, _, _ in self.call_edges:
            self._callees[src].add(dst)
            self._callers[dst].add(src)

    # ------------------------------------------------------------------
    # 問い合わせ
    # ------------------------------------------------------------------
    def modules(self) -> dict[str, list[str]]:
        return self._modules

    def module_deps(self) -> dict[str, dict[str, int]]:
        """モジュール間依存（依存元 → {依存先: import 数}）。"""
        deps: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for src, targets in self.file_deps.items():
            for dst in targets:
                a, b = module_of(src), module_of(dst)
                if a != b:
                    deps[a][b] += 1
        return {k: dict(v) for k, v in deps.items()}

    def symbols_in(self, path: str) -> list[Symbol]:
        return self.files[path].symbols if path in self.files else []

    def children(self, symbol_id: str) -> list[Symbol]:
        return [s for s in self.symbols_in(symbol_id.split("::")[0]) if s.parent == symbol_id]

    def callees(self, symbol_id: str) -> list[str]:
        return sorted(self._callees.get(symbol_id, ()))

    def callers(self, symbol_id: str) -> list[str]:
        return sorted(self._callers.get(symbol_id, ()))

    def call_lines(self, caller_id: str, callee_id: str) -> list[int]:
        return [line for src, dst, line, _ in self.call_edges if src == caller_id and dst == callee_id]

    def dependents(self, path: str) -> list[str]:
        """path を import しているファイル。"""
        return sorted(self._dependents.get(path, ()))

    def source_of(self, path: str) -> str:
        if path not in self._source_cache:
            self._source_cache[path] = read_source(os.path.join(self.root, path))
        return self._source_cache[path]

    def symbol_source(self, symbol_id: str) -> str:
        sym = self.symbols[symbol_id]
        lines = self.source_of(sym.file).split("\n")
        return "\n".join(lines[sym.start_line - 1: sym.end_line])

    def entry_points(self) -> list[str]:
        """エントリポイントと思われるファイル。"""
        entries = []
        for path, pf in self.files.items():
            names = {s.name for s in pf.symbols if s.parent is None}
            if pf.lang == "python":
                if re.search(r"__name__\s*==\s*['\"]__main__['\"]", self.source_of(path)) \
                        or path.endswith("__main__.py"):
                    entries.append(path)
            elif pf.lang == "dart" and "main" in names:
                entries.append(path)
            elif pf.lang in ("javascript", "typescript") and (
                    re.search(r"(^|/)(index|main|server|app|cli)\.(t|j)sx?$", path) and not self.dependents(path)):
                entries.append(path)
        pkg = Path(self.root) / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                bins = data.get("bin", {})
                for target in ([data.get("main")] + (list(bins.values()) if isinstance(bins, dict) else [bins])):
                    if isinstance(target, str):
                        rel = os.path.normpath(target).replace(os.sep, "/")
                        if rel in self.files and rel not in entries:
                            entries.append(rel)
            except (OSError, ValueError):
                pass
        return sorted(entries)

    def cycles(self, graph: dict[str, set[str]] | None = None) -> list[list[str]]:
        """強連結成分（2 要素以上）を循環として返す。graph 省略時はモジュール依存。"""
        if graph is None:
            graph = {k: set(v) for k, v in self.module_deps().items()}
        index, low, on_stack, stack, result = {}, {}, set(), [], []
        counter = [0]

        def strongconnect(v):
            index[v] = low[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on_stack.add(v)
            for w in graph.get(v, ()):
                if w not in index:
                    strongconnect(w)
                    low[v] = min(low[v], low[w])
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))

        sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
        nodes = set(graph) | {w for vs in graph.values() for w in vs}
        for v in sorted(nodes):
            if v not in index:
                strongconnect(v)
        return sorted(result)

    def module_metrics(self) -> dict[str, dict]:
        deps = self.module_deps()
        fan_in: dict[str, int] = defaultdict(int)
        for a, targets in deps.items():
            for b in targets:
                fan_in[b] += 1
        metrics = {}
        for mod, paths in self.modules().items():
            fo, fi = len(deps.get(mod, {})), fan_in.get(mod, 0)
            metrics[mod] = {
                "files": len(paths),
                "loc": sum(self.files[p].loc for p in paths),
                "symbols": sum(len(self.files[p].symbols) for p in paths),
                "fan_in": fi,
                "fan_out": fo,
                "instability": round(fo / (fi + fo), 2) if fi + fo else 0.0,
                "external": sorted({e for p in paths for e in self.external_deps.get(p, ())}),
            }
        return metrics

    def find_symbol(self, ref: str) -> Symbol | None:
        """ID・修飾名・名前のいずれかでシンボルを探す（根拠チェック用）。"""
        if ref in self.symbols:
            return self.symbols[ref]
        name = ref.split("::")[-1]
        for s in self._by_name.get(name.split(".")[-1], []):
            if s.qualname == name:
                return s
        cands = self._by_name.get(name.split(".")[-1], [])
        return cands[0] if len(cands) == 1 else None
