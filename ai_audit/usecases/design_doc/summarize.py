"""
S2: ファイル要約 / S3: モジュール要約 / S4: システム要約
"""
from ...context.builder import number_lines
from ...core.budget import TIER_LARGE
from ...index.graph import CodeGraph, module_of
from ...pipeline.llm_steps import compact_json, json_step, new_builder, reduce_json
from ...wears import design as W
from .facts import facts_text


# ---------------------------------------------------------------------------
# S2: ファイル要約
# ---------------------------------------------------------------------------
def _symbol_table(graph: CodeGraph, path: str, top_only: bool = False, max_rows: int = 0,
                  with_signature: bool = True) -> str:
    rows = []
    syms = [s for s in graph.files[path].symbols if not (top_only and s.parent)]
    for s in syms[:max_rows or None]:
        sig = f": {s.signature[:120]}" if with_signature else ""
        rows.append(f"- {s.qualname} ({s.kind}) L{s.start_line}-{s.end_line}{sig}")
    if max_rows and len(syms) > max_rows:
        rows.append(f"- …他 {len(syms) - max_rows} 件")
    return "\n".join(rows) or "（シンボルなし）"


def _file_header(graph: CodeGraph, path: str) -> list[tuple[str, str, object]]:
    pf = graph.files[path]
    deps = sorted(graph.file_deps.get(path, ()))
    users = graph.dependents(path)
    info = [f"パス: {path}（{pf.lang}, {pf.loc} 行）"]
    if deps:
        info.append("import しているリポジトリ内ファイル: " + ", ".join(deps[:30]))
    if users:
        info.append("このファイルを import しているファイル: " + ", ".join(users[:30]))
    ext = sorted(graph.external_deps.get(path, ()))
    if ext:
        info.append("外部ライブラリ: " + ", ".join(ext[:30]))
    if pf.error:
        info.append(f"解析の注意: {pf.error}")
    return [
        ("info", "ファイル情報", "\n".join(info)),
        ("symbols", "シンボル一覧（行番号は正確）",
         [_symbol_table(graph, path), _symbol_table(graph, path, top_only=True),
          _symbol_table(graph, path, top_only=True, max_rows=30, with_signature=False)]),
    ]


def _related_parts(graph: CodeGraph, path: str) -> list[tuple[str, str]]:
    """T3: import 先・import 元のスケルトン（関連文脈）。"""
    parts = []
    for rel in sorted(graph.file_deps.get(path, ()))[:8] + graph.dependents(path)[:4]:
        skel = graph.files[rel].skeleton
        if skel:
            parts.append((rel, skel))
    return parts


def _split_by_symbols(graph: CodeGraph, path: str, source: str, rt, system: str,
                      header_tokens: int) -> list[str]:
    """ファイルが大きすぎる場合、トップレベルシンボルの境界で行番号付きソースを分割する。"""
    lines = source.split("\n")
    tops = [s for s in graph.files[path].symbols if s.parent is None]
    bounds = sorted({1} | {s.start_line for s in tops})
    segments = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] - 1 if i + 1 < len(bounds) else len(lines)
        if end >= start:
            segments.append((start, end))
    limit = max(200, int((new_builder(rt, system).budget - header_tokens) * 0.85))
    chunks, cur_start, cur_end = [], None, None
    for start, end in segments:
        if cur_start is None:
            cur_start, cur_end = start, end
            continue
        text = number_lines("\n".join(lines[cur_start - 1:end]), cur_start)
        if rt.count(text) > limit:
            chunks.append((cur_start, cur_end))
            cur_start, cur_end = start, end
        else:
            cur_end = end
    if cur_start is not None:
        chunks.append((cur_start, cur_end))
    out = []
    for start, end in chunks:
        text = number_lines("\n".join(lines[start - 1:end]), start)
        # 1 シンボルだけで予算を超える場合は行単位で強制分割する
        while rt.count(text) > limit and end > start:
            mid = start + max(1, (end - start) // 2)
            out.append(number_lines("\n".join(lines[start - 1:mid]), start))
            start = mid + 1
            text = number_lines("\n".join(lines[start - 1:end]), start)
        out.append(text)
    return out


def _fix_lines(graph: CodeGraph, path: str, summary: dict) -> dict:
    """key_symbols の行番号を Code Graph の正確な値で上書きし、存在しないシンボルを除く。"""
    by_name = {}
    for s in graph.files[path].symbols:
        by_name.setdefault(s.qualname, s)
        by_name.setdefault(s.name, s)
    fixed = []
    for ks in summary.get("key_symbols") or []:
        if not isinstance(ks, dict):
            continue
        name = str(ks.get("name", "")).split("::")[-1].replace("()", "")
        sym = by_name.get(name) or by_name.get(name.split(".")[-1])
        if sym is None:
            continue
        ks["name"], ks["lines"] = sym.qualname, f"{sym.start_line}-{sym.end_line}"
        fixed.append(ks)
    summary["key_symbols"] = fixed
    return summary


def summarize_file(pipe, rt, graph: CodeGraph, path: str) -> dict:
    system = W.with_rules(W.FILE_SUMMARY, rt.tier, pipe.lang)
    source = graph.source_of(path)
    pf = graph.files[path]
    header = _file_header(graph, path)

    b = new_builder(rt, system)
    for key, title, text in header:
        b.add(key, title, text, required=True)
    alts = [number_lines(source)] + ([pf.skeleton] if pf.skeleton else [])
    b.add("source", "ソース（行番号付き）", alts, priority=10)
    if rt.tier == TIER_LARGE:
        for rel, skel in _related_parts(graph, path):
            b.add(f"related:{rel}", f"関連ファイルのスケルトン: {rel}", skel, priority=40)
    built = b.build(strict=False)

    if "source" not in built.dropped:
        mode = "skeleton" if "source" in built.degraded else "full"
        out = json_step(pipe, rt, f"s2/{path}", system, b, W.FILE_SUMMARY_SCHEMA)
    else:
        mode = "split"
        # 分割時のヘッダはファイル情報＋トップレベルのシンボル（行番号のみ）に絞る
        header = [header[0], ("symbols", "トップレベルのシンボル（行番号は正確）",
                              [_symbol_table(graph, path, top_only=True, with_signature=False),
                               _symbol_table(graph, path, top_only=True, max_rows=30, with_signature=False)])]
        hb = new_builder(rt, system)
        for k, title, text in header:
            hb.add(k, title, text, required=True)
        chunks = _split_by_symbols(graph, path, source, rt, system, hb.build(strict=False).tokens)
        items = [(f"部分{i + 1}", [c]) for i, c in enumerate(chunks)]
        partials = []
        for i, (key, alts_) in enumerate(items):
            pb = new_builder(rt, system)
            for k, title, text in header:
                pb.add(k, title, text, required=True)
            pb.add("source", f"ソースの一部（{key}、行番号付き）", alts_, priority=10)
            res = json_step(pipe, rt, f"s2/{path}/part{i}", system, pb, W.FILE_SUMMARY_SCHEMA)
            partials.append((key, [compact_json(res["result"])]))
        merge_system = W.with_rules(W.FILE_MERGE, rt.tier, pipe.lang)
        out = reduce_json(pipe, rt, f"s2/{path}/merge", merge_system, merge_system,
                          header[:1], partials, W.FILE_SUMMARY_SCHEMA, item_title="部分要約")
    return {"path": path, "mode": mode, "summary": _fix_lines(graph, path, out["result"]),
            "dropped": out["dropped"], "degraded": out["degraded"]}


def run_s2(pipe, rt, graph: CodeGraph) -> dict[str, dict]:
    paths = sorted(graph.files)
    pipe.stage("S2 ファイル要約", len(paths))
    results = pipe.map(
        paths,
        name_fn=lambda p: f"s2-file/{p}",
        inputs_fn=lambda p: {"sha": graph.files[p].sha256, "deps": sorted(graph.file_deps.get(p, ())),
                             "users": graph.dependents(p), "prompt": W.PROMPT_VERSION,
                             "budget": rt.budget.input_budget, "lang": pipe.lang},
        fn=lambda p: summarize_file(pipe, rt, graph, p),
    )
    return {p: r for p, r in zip(paths, results) if r}


# ---------------------------------------------------------------------------
# S3: モジュール要約
# ---------------------------------------------------------------------------
def _file_items(file_summaries: dict, paths: list[str]) -> list[tuple[str, list[str]]]:
    items = []
    for p in paths:
        fs = file_summaries.get(p)
        if not fs:
            continue
        s = fs["summary"]
        compact = {"purpose": s.get("purpose"), "responsibilities": s.get("responsibilities"),
                   "key_symbols": [k.get("name") for k in s.get("key_symbols", [])]}
        items.append((p, [compact_json(s), compact_json(compact)]))
    return items


def _module_header(graph: CodeGraph, facts: dict, module: str) -> list[tuple[str, str, object]]:
    m = facts["modules"][module]
    deps_out = facts["module_deps"].get(module, {})
    deps_in = {a: t[module] for a, t in facts["module_deps"].items() if module in t}
    files = graph.modules()[module]
    info = [
        f"モジュール: {module}（{m['files']} ファイル, {m['loc']} 行, {m['symbols']} シンボル）",
        "ファイル: " + ", ".join(files[:50]),
        "依存先: " + (", ".join(f"{k}({v})" for k, v in deps_out.items()) or "なし"),
        "依存元: " + (", ".join(f"{k}({v})" for k, v in deps_in.items()) or "なし"),
        "外部ライブラリ: " + (", ".join(m["external"][:30]) or "なし"),
    ]
    return [("module", "モジュールの事実", "\n".join(info))]


def run_s3(pipe, rt, graph: CodeGraph, facts: dict, file_summaries: dict) -> dict[str, dict]:
    modules = graph.modules()
    names = sorted(modules)
    pipe.stage("S3 モジュール要約", len(names))
    system = W.with_rules(W.MODULE_SUMMARY, rt.tier, pipe.lang)
    merge = W.with_rules(W.MODULE_MERGE, rt.tier, pipe.lang)

    def run(module):
        items = _file_items(file_summaries, modules[module])
        out = reduce_json(pipe, rt, f"s3/{module}", system, merge, _module_header(graph, facts, module),
                          items, W.MODULE_SUMMARY_SCHEMA, item_title="ファイル要約")
        return {"module": module, "summary": out["result"], "dropped": out["dropped"],
                "degraded": out["degraded"],
                "missing_files": [p for p in modules[module] if p not in file_summaries]}

    results = pipe.map(
        names,
        name_fn=lambda m: f"s3-module/{m}",
        inputs_fn=lambda m: {"files": {p: file_summaries.get(p, {}).get("summary") for p in modules[m]},
                             "facts": facts["modules"][m], "prompt": W.PROMPT_VERSION,
                             "budget": rt.budget.input_budget, "lang": pipe.lang},
        fn=run,
    )
    return {m: r for m, r in zip(names, results) if r}


# ---------------------------------------------------------------------------
# S4: システム要約
# ---------------------------------------------------------------------------
def run_s4(pipe, rt, facts: dict, module_summaries: dict) -> dict:
    pipe.stage("S4 システム要約")
    system = W.with_rules(W.SYSTEM_SUMMARY, rt.tier, pipe.lang)
    merge = W.with_rules(W.SYSTEM_MERGE, rt.tier, pipe.lang)
    items = []
    for mod, ms in sorted(module_summaries.items()):
        s = ms["summary"]
        compact = {"purpose": s.get("purpose"), "responsibilities": s.get("responsibilities")}
        items.append((mod, [compact_json(s), compact_json(compact)]))
    header = [("facts", "静的解析の事実", [facts_text(facts), facts_text(facts, include_readme=False)])]

    def run():
        out = reduce_json(pipe, rt, "s4/system", system, merge, header, items,
                          W.SYSTEM_SUMMARY_SCHEMA, item_title="モジュール要約")
        return {"summary": out["result"], "dropped": out["dropped"], "degraded": out["degraded"]}

    return pipe.step("s4-system", {"modules": {m: v["summary"] for m, v in module_summaries.items()},
                                   "facts": facts_text(facts), "prompt": W.PROMPT_VERSION,
                                   "budget": rt.budget.input_budget, "lang": pipe.lang}, run)


def modules_of_files(paths) -> set[str]:
    return {module_of(p) for p in paths}
