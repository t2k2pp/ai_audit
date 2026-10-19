"""
S0/S1: インデックス更新と「事実」の抽出（LLM を使わない）

事実 = 統計、モジュール一覧とメトリクス、モジュール依存、循環、外部依存、
       エントリポイント、ディレクトリツリー、README・マニフェストの抜粋、git の HEAD
"""
import json
import os
from collections import Counter
from pathlib import Path

from ...index.gitinfo import head_info
from ...index.graph import CodeGraph
from ...render import mermaid

_MANIFESTS = ["package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
              "pubspec.yaml", "tsconfig.json", "Dockerfile", "docker-compose.yml"]
_README_CHARS = 4000


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _manifest_summary(root: Path) -> dict:
    found = {}
    for name in _MANIFESTS:
        p = root / name
        if not p.exists():
            continue
        if name == "package.json":
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                found[name] = json.dumps({k: data.get(k) for k in (
                    "name", "description", "main", "bin", "scripts", "dependencies", "devDependencies")
                    if data.get(k)}, ensure_ascii=False)[:2000]
                continue
            except (OSError, ValueError):
                pass
        found[name] = _read(p, 1500)
    return found


def _tree(modules: dict[str, list[str]], max_lines: int = 60) -> str:
    lines = []
    for mod, files in modules.items():
        lines.append(f"{mod}/ ({len(files)} files)")
        if len(lines) >= max_lines:
            lines.append(f"... 他 {len(modules) - len(lines)} モジュール")
            break
    return "\n".join(lines)


def collect_facts(graph: CodeGraph) -> dict:
    root = Path(graph.root)
    modules = graph.modules()
    readme = next((p for p in sorted(root.glob("README*")) if p.is_file()), None)
    langs = Counter(pf.lang for pf in graph.files.values())
    external = sorted({e for deps in graph.external_deps.values() for e in deps})
    return {
        "project": {
            "name": root.name,
            "readme": _read(readme, _README_CHARS) if readme else "",
            "manifests": _manifest_summary(root),
            "git": head_info(str(root)),
        },
        "stats": {
            "files": len(graph.files),
            "loc": sum(pf.loc for pf in graph.files.values()),
            "symbols": len(graph.symbols),
            "langs": dict(langs),
            "parse_errors": {p: pf.error for p, pf in graph.files.items() if pf.error},
        },
        "modules": graph.module_metrics(),
        "module_deps": graph.module_deps(),
        "cycles": graph.cycles(),
        "entry_points": graph.entry_points(),
        "external_deps": external,
        "tree": _tree(modules),
    }


def build_diagrams(graph: CodeGraph, facts: dict) -> dict[str, str]:
    """diagrams/*.mmd の内容（ファイル名 → Mermaid）。"""
    diagrams = {"module_dependencies.mmd": mermaid.module_dependency_graph(
        facts["module_deps"], list(facts["modules"]), facts["cycles"])}
    all_classes = {s.name: s.id for s in graph.symbols.values() if s.kind == "class"}
    for mod, files in graph.modules().items():
        syms = [s for f in files for s in graph.files[f].symbols]
        diagram = mermaid.class_diagram(syms, all_classes)
        if diagram:
            diagrams[f"classes_{module_slug(mod)}.mmd"] = diagram
    return diagrams


def module_slug(module: str) -> str:
    return module.replace("/", "__").replace("(", "").replace(")", "") or "root"


def facts_text(facts: dict, include_readme: bool = True) -> str:
    """プロンプト用の事実テキスト（コンパクトな表記）。"""
    st = facts["stats"]
    lines = [
        f"プロジェクト名: {facts['project']['name']}",
        f"規模: {st['files']} ファイル / {st['loc']} 行 / {st['symbols']} シンボル / 言語 {st['langs']}",
        f"エントリポイント: {', '.join(facts['entry_points']) or '不明'}",
        f"外部依存: {', '.join(facts['external_deps'][:60]) or 'なし'}",
        "モジュール（ファイル数, 行数, fan-in, fan-out, 不安定度）:",
    ]
    for mod, m in facts["modules"].items():
        lines.append(f"- {mod}: {m['files']}, {m['loc']}, {m['fan_in']}, {m['fan_out']}, {m['instability']}")
    lines.append("モジュール依存（依存元 -> 依存先(import数)）:")
    for a, targets in facts["module_deps"].items():
        lines.append(f"- {a} -> " + ", ".join(f"{b}({c})" for b, c in targets.items()))
    if facts["cycles"]:
        lines.append("循環依存: " + " / ".join(" ⇄ ".join(c) for c in facts["cycles"]))
    if include_readme and facts["project"]["readme"]:
        lines.append("README 抜粋:\n" + facts["project"]["readme"][:1500])
    return "\n".join(lines)


def update_index(directory: str, log=print) -> CodeGraph:
    graph = CodeGraph(directory)
    stats = graph.update()
    log(f"[INFO] インデックス更新: 追加 {stats['added']} / 変更 {stats['changed']} / "
        f"削除 {stats['removed']} / 変更なし {stats['unchanged']}")
    errors = {p: pf.error for p, pf in graph.files.items() if pf.error}
    for p, e in list(errors.items())[:10]:
        log(f"[WARN] {p}: {e}")
    return graph


def rel_display(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")
