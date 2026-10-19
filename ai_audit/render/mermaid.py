"""
Mermaid 図の機械生成（Code Graph から。LLM に推測させない）
"""
import re

MAX_NODES = 40


def _nid(name: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def _label(name: str) -> str:
    return name.replace('"', "'")


def module_dependency_graph(module_deps: dict[str, dict[str, int]], modules: list[str],
                            cycles: list[list[str]] | None = None) -> str:
    """モジュール依存図（graph LR）。循環に含まれるモジュールは強調する。"""
    nodes = sorted(set(modules) | set(module_deps) | {b for v in module_deps.values() for b in v})
    if len(nodes) > MAX_NODES:
        # 依存の多いモジュールを優先して表示する
        weight = {n: 0 for n in nodes}
        for a, targets in module_deps.items():
            for b, c in targets.items():
                weight[a] += c
                weight[b] += c
        nodes = sorted(sorted(nodes, key=lambda n: -weight[n])[:MAX_NODES])
    in_cycle = {m for c in (cycles or []) for m in c}
    lines = ["graph LR"]
    for n in nodes:
        lines.append(f'  {_nid(n)}["{_label(n)}"]')
    shown = set(nodes)
    for a in sorted(module_deps):
        for b, count in sorted(module_deps[a].items()):
            if a in shown and b in shown:
                lines.append(f"  {_nid(a)} -->|{count}| {_nid(b)}")
    if in_cycle & shown:
        lines.append("  classDef cycle fill:#fdd,stroke:#c00")
        lines.append("  class " + ",".join(_nid(n) for n in sorted(in_cycle & shown)) + " cycle")
    return "\n".join(lines)


def class_diagram(symbols: list, all_classes: dict[str, str] | None = None,
                  max_members: int = 12) -> str:
    """
    クラス図（classDiagram）。symbols は対象範囲のシンボル（Symbol）のリスト。
    all_classes: クラス名 → シンボル ID（範囲外の継承元を解決するため）
    """
    classes = [s for s in symbols if s.kind in ("class", "interface", "enum")]
    if not classes:
        return ""
    classes = classes[:MAX_NODES]
    by_parent: dict[str, list] = {}
    for s in symbols:
        if s.parent:
            by_parent.setdefault(s.parent, []).append(s)
    names = {c.name for c in classes}
    lines = ["classDiagram"]
    for c in classes:
        cid = _nid(c.qualname)
        lines.append(f'  class {cid}["{_label(c.name)}"]')
        if c.kind == "interface":
            lines.append(f"  <<interface>> {cid}")
        elif c.kind == "enum":
            lines.append(f"  <<enumeration>> {cid}")
        members = [m for m in by_parent.get(c.id, []) if m.exported or m.name in ("__init__", "constructor")]
        for m in members[:max_members]:
            name = re.sub(r"[^\w]", "", m.name) or "member"
            lines.append(f"  {cid} : +{name}()")
        if len(members) > max_members:
            lines.append(f"  {cid} : ...他 {len(members) - max_members} 件")
    for c in classes:
        for base in c.bases:
            base_name = re.split(r"[\[<(]", base.split(".")[-1])[0].strip()
            if base_name in names:
                target = next(x for x in classes if x.name == base_name)
                lines.append(f"  {_nid(target.qualname)} <|-- {_nid(c.qualname)}")
            elif all_classes and base_name in all_classes:
                lines.append(f'  class {_nid(base_name)}["{_label(base_name)}"]')
                lines.append(f"  {_nid(base_name)} <|-- {_nid(c.qualname)}")
    return "\n".join(lines)


def fenced(diagram: str) -> str:
    return f"```mermaid\n{diagram}\n```" if diagram else ""
