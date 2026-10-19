"""
ユースケースA v2: 多重マイクロ監査

監査単位: Code Graph の関数・メソッド（クラスとメソッドを二重に監査しない）＋モジュール直下のコード
ウェア: security / readability / performance（全ティア）、contract（呼び出し元がある場合・T2 以上）
文脈: T2 以上は呼び出し先のシグネチャと呼び出し元の該当箇所、T3 はさらに呼び出し先のソースも同梱
検証: T2 以上は検証ウェアで各指摘を再確認し、成り立たない指摘を落とす
キャッシュ: ステップの入力 = 送信プロンプト（コード＋文脈＋モデル＋ウェア版数）。変更がなければ再監査しない
"""
import json
import os
import subprocess
import sys
from dataclasses import dataclass

from ..context.builder import number_lines
from ..core.budget import TIER_COMPACT, TIER_LARGE
from ..core.runtime import Runtime, create_runtime
from ..index.graph import CodeGraph
from ..index.scanner import to_rel
from ..pipeline.engine import Pipeline, StepFailed
from ..pipeline.jobstore import JobStore, atomic_write, work_base
from ..pipeline.llm_steps import compact_json, json_step, new_builder
from ..wears import audit as WA
from .design_doc.assemble import now_text

BASE_WEARS = ["security", "readability", "performance"]
_SEVERITIES = ("high", "medium", "low")


@dataclass
class Unit:
    id: str
    file: str
    name: str
    kind: str
    lang: str
    start: int
    end: int
    symbol_id: str | None = None


def find_root(path: str) -> str:
    """単一ファイル監査の基準ディレクトリ（git のルート、なければファイルのディレクトリ）。"""
    d = os.path.dirname(os.path.abspath(path))
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.abspath(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return d


def units_for_file(graph: CodeGraph, path: str, max_lines: int) -> list[Unit]:
    pf = graph.files[path]
    units = []
    for s in pf.symbols:
        if s.kind not in ("function", "method"):
            continue
        # 長すぎる関数は行単位で分割する
        start = s.start_line
        part = 1
        while start <= s.end_line:
            end = min(s.end_line, start + max_lines - 1)
            uid = s.id if (start == s.start_line and end == s.end_line) else f"{s.id}#part{part}"
            units.append(Unit(uid, path, s.qualname, s.kind, s.lang, start, end, s.id))
            start, part = end + 1, part + 1
    # モジュール直下のコード（どのトップレベルシンボルにも含まれない行）
    covered = set()
    for s in pf.symbols:
        if s.parent is None:
            covered.update(range(s.start_line, s.end_line + 1))
    lines = graph.source_of(path).split("\n")
    loose = [i for i in range(1, len(lines) + 1)
             if i not in covered and lines[i - 1].strip() and not lines[i - 1].strip().startswith(("#", "//", "import ", "from "))]
    if len(loose) >= 5:
        units.append(Unit(f"{path}::<module>", path, "<module>", "module", pf.lang,
                          loose[0], min(loose[-1], loose[0] + max_lines - 1)))
    return units


def _unit_code(graph: CodeGraph, unit: Unit) -> str:
    lines = graph.source_of(unit.file).split("\n")
    return number_lines("\n".join(lines[unit.start - 1:unit.end]), unit.start)


def _context_parts(graph: CodeGraph, unit: Unit, tier: str) -> list[tuple[str, str, object, int]]:
    """(key, title, text, priority) のリスト。"""
    if tier == TIER_COMPACT or not unit.symbol_id:
        return []
    parts = []
    for callee_id in graph.callees(unit.symbol_id)[:10]:
        callee = graph.symbols[callee_id]
        sig = f"{callee.signature}\n{callee.doc[:200]}".strip()
        if tier == TIER_LARGE:
            src = number_lines(graph.symbol_source(callee_id), callee.start_line)
            parts.append((f"callee:{callee_id}", f"呼び出し先: {callee_id}", [src, sig], 40))
        else:
            parts.append((f"callee:{callee_id}", f"呼び出し先: {callee_id}", sig, 40))
    callers = graph.callers(unit.symbol_id)
    for caller_id in callers[:6 if tier == TIER_LARGE else 3]:
        caller = graph.symbols[caller_id]
        lines = graph.source_of(caller.file).split("\n")
        call_lines = graph.call_lines(caller_id, unit.symbol_id)
        snippets = []
        for ln in call_lines[:2]:
            a, b = max(caller.start_line, ln - 4), min(caller.end_line, ln + 4)
            snippets.append(number_lines("\n".join(lines[a - 1:b]), a))
        parts.append((f"caller:{caller_id}", f"呼び出し元: {caller_id}（{caller.file}）",
                      "\n...\n".join(snippets), 30))
    return parts


def _normalize(issues: list, wear: str, unit: Unit) -> list[dict]:
    out = []
    for it in issues:
        if not isinstance(it, dict) or not it.get("description"):
            continue
        try:
            line = int(str(it.get("line", unit.start)).split("-")[0])
        except ValueError:
            line = unit.start
        approx = not unit.start <= line <= unit.end
        if approx:
            line = unit.start
        sev = str(it.get("severity", "low")).lower()
        out.append({
            "type": wear,
            "severity": sev if sev in _SEVERITIES else "low",
            "line": line,
            "line_number_offset": line - unit.start,   # v0.3 互換
            "line_approximate": approx,
            "title": it.get("title", ""),
            "description": it.get("description", ""),
            "suggestion": it.get("suggestion", ""),
            "confidence": str(it.get("confidence", "medium")).lower(),
        })
    return out


def audit_unit(pipe, rt, graph: CodeGraph, unit: Unit, verify: bool) -> dict:
    code = _unit_code(graph, unit)
    ctx_parts = _context_parts(graph, unit, rt.tier)
    wears = list(BASE_WEARS)
    if rt.tier != TIER_COMPACT and any(k.startswith("caller:") for k, *_ in ctx_parts):
        wears.append("contract")
    issues: list[dict] = []
    errors = []
    for wear in wears:
        system = WA.wear_prompt(wear, unit.lang)
        b = new_builder(rt, system)
        b.add("target", f"監査対象: {unit.id}（{unit.lang}）", code, required=True)
        for key, title, text, prio in ctx_parts:
            # 呼び出し元は入力の出どころ・契約の判断に使うウェアにだけ渡す
            if key.startswith("caller:") and wear not in ("security", "contract"):
                continue
            b.add(key, f"参考 {title}", text, priority=prio)
        try:
            out = json_step(pipe, rt, f"audit/{unit.id}/{wear}", system, b, WA.ISSUES_SCHEMA)
            issues += _normalize(out["result"].get("issues", []), wear, unit)
        except StepFailed as e:
            errors.append(f"{wear}: {e}")

    if verify and issues and rt.tier != TIER_COMPACT:
        b = new_builder(rt, WA.VERIFIER)
        b.add("target", f"コード: {unit.id}", code, required=True)
        listing = "\n".join(f"{i}. [{x['type']}/{x['severity']}] 行{x['line']}: {x['description']}"
                            for i, x in enumerate(issues))
        b.add("issues", "指摘の一覧", listing, required=True)
        try:
            out = json_step(pipe, rt, f"audit/{unit.id}/verify", WA.VERIFIER, b, WA.VERDICTS_SCHEMA)
            invalid = set()
            for v in out["result"].get("verdicts", []):
                try:
                    if v.get("valid") is False:
                        invalid.add(int(v.get("index")))
                except (TypeError, ValueError):
                    continue
            issues = [x for i, x in enumerate(issues) if i not in invalid]
        except StepFailed as e:
            errors.append(f"verify: {e}")
    return {"unit": unit.__dict__, "issues": issues, "errors": errors}


def _run(graph: CodeGraph, paths: list[str], rt: Runtime, force: bool, verify: bool,
         quiet: bool) -> dict[str, list[dict]]:
    pipe = Pipeline("audit", JobStore(work_base(graph.root), "audit"), rt, force=force,
                    version=WA.PROMPT_VERSION, quiet=quiet)
    # 1 単位のコードは入力予算の半分まで（残りは文脈とシステムプロンプト）
    max_lines = max(40, rt.budget.input_budget // 2 // 12)
    units = [u for p in paths for u in units_for_file(graph, p, max_lines)]
    pipe.stage("監査", len(units))
    results = pipe.map(
        units,
        name_fn=lambda u: f"audit-unit/{u.id}",
        inputs_fn=lambda u: {"code": _unit_code(graph, u), "ctx": [t for *_, t, _ in _context_parts(graph, u, rt.tier)],
                             "v": WA.PROMPT_VERSION, "verify": verify, "budget": rt.budget.input_budget},
        fn=lambda u: audit_unit(pipe, rt, graph, u, verify),
        label_fn=lambda u: f"{u.file} {u.name}",
    )
    pipe.finish()
    by_file: dict[str, list[dict]] = {p: [] for p in paths}
    for u, r in zip(units, results):
        if r is None:
            r = {"unit": u.__dict__, "issues": [], "errors": ["監査に失敗しました（再実行で再試行）"]}
        by_file[u.file].append(r)
    return by_file


def _file_report(graph: CodeGraph, rel: str, unit_results: list[dict], rt: Runtime) -> dict:
    chunks = []
    for r in unit_results:
        u = r["unit"]
        chunks.append({"chunk_id": u["id"], "name": u["name"], "kind": u["kind"],
                       "start_line": u["start"], "end_line": u["end"],
                       "issues": r["issues"], "errors": r["errors"]})
    return {
        "source_file": os.path.join(graph.root, rel),
        "generated_at": now_text(),
        "model": rt.settings.model_name,
        "tier": rt.tier,
        "chunks": chunks,
        "total_issues": sum(len(c["issues"]) for c in chunks),
    }


def _audit_json_path(abs_file: str) -> str:
    return os.path.splitext(abs_file)[0] + "_audit.json"


def audit_file(file_path: str, force: bool = False, runtime: Runtime | None = None,
               verify: bool = True, root: str | None = None, quiet: bool = False) -> dict:
    """単一ファイルを監査し、<ファイル名>_audit.json を書き出してその内容を返す。"""
    abs_file = os.path.abspath(file_path)
    if not os.path.isfile(abs_file):
        raise FileNotFoundError(f"ファイルが見つかりません: {abs_file}")
    rt = runtime or create_runtime(quiet=quiet)
    graph = CodeGraph(root or find_root(abs_file))
    graph.update()
    rel = to_rel(abs_file, graph.root)
    if rel not in graph.files:
        raise ValueError(f"監査対象外のファイルです（未対応の言語、または除外設定）: {abs_file}")
    by_file = _run(graph, [rel], rt, force, verify, quiet)
    report = _file_report(graph, rel, by_file[rel], rt)
    out = _audit_json_path(abs_file)
    atomic_write(out, json.dumps(report, ensure_ascii=False, indent=2))
    if not quiet:
        print(f"[INFO] 監査結果を保存しました: {out}（{report['total_issues']} 件）")
    rt.close()
    return report


def audit_directory(directory: str, force: bool = False, output_dir: str | None = None,
                    runtime: Runtime | None = None, verify: bool = True, sarif: str | None = None,
                    quiet: bool = False) -> dict:
    """ディレクトリ配下を一括監査し、ファイルごとの JSON・サマリー JSON・（指定時）SARIF を出力する。"""
    abs_dir = os.path.abspath(directory)
    if not os.path.isdir(abs_dir):
        raise FileNotFoundError(f"ディレクトリが見つかりません: {abs_dir}")
    rt = runtime or create_runtime(quiet=quiet)
    graph = CodeGraph(abs_dir)
    graph.update()
    paths = sorted(graph.files)
    if not paths:
        raise ValueError("対象のソースファイルが見つかりませんでした。")
    by_file = _run(graph, paths, rt, force, verify, quiet)
    out_root = os.path.abspath(output_dir) if output_dir else abs_dir
    reports = {}
    for rel in paths:
        report = _file_report(graph, rel, by_file[rel], rt)
        reports[rel] = report
        target = os.path.join(out_root, rel) if output_dir else os.path.join(abs_dir, rel)
        atomic_write(_audit_json_path(target), json.dumps(report, ensure_ascii=False, indent=2))

    files_summary = sorted(({
        "file": rel,
        "total_issues": r["total_issues"],
        "high_severity": sum(1 for c in r["chunks"] for i in c["issues"] if i["severity"] == "high"),
    } for rel, r in reports.items()), key=lambda x: (-x["total_issues"], x["file"]))
    summary = {
        "directory": abs_dir, "generated_at": now_text(), "model": rt.settings.model_name,
        "tier": rt.tier, "total_files": len(paths),
        "audited_files": sum(1 for r in reports.values() if r["chunks"]),
        "total_issues": sum(f["total_issues"] for f in files_summary),
        "high_severity_total": sum(f["high_severity"] for f in files_summary),
        "files": files_summary,
    }
    atomic_write(os.path.join(out_root, "_summary_audit.json"), json.dumps(summary, ensure_ascii=False, indent=2))
    if sarif:
        from ..render.sarif import to_sarif
        atomic_write(os.path.abspath(sarif), json.dumps(to_sarif(reports, abs_dir), ensure_ascii=False, indent=2))
    if not quiet:
        print(f"[INFO] 一括監査完了: {len(paths)} ファイル / {summary['total_issues']} 件"
              f"（high {summary['high_severity_total']} 件）")
    errors = [c for r in reports.values() for c in r["chunks"] if c["errors"]]
    if errors:
        print(f"[WARN] {len(errors)} 単位で監査に失敗しました。再実行すると失敗した部分だけ再試行します。",
              file=sys.stderr)
    rt.close()
    return summary
