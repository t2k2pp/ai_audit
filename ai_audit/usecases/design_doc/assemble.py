"""
S7: 組み立てと出力

v2 レイアウト（既定、<対象>/_design/）:
  README.md / overview.md / detail.md / modules/<モジュール>.md / diagrams/*.mmd / adr/*.md / _meta/coverage.json
legacy レイアウト（v0.3 互換）:
  _design_detail.md（詳細＋モジュール別を連結） / _design_overview.md
"""
import json
import os
import re
from datetime import datetime

from ...index.graph import CodeGraph
from ...pipeline.jobstore import atomic_write
from ...render.mermaid import fenced
from .facts import module_slug
from .sections import CITATION_RE

_FAILED = "*この章の生成に失敗しました。もう一度実行すると、失敗した部分から生成します。*"


def link_citations(text: str, root: str, doc_dir: str) -> str:
    """[パス:行] を、ソースへの相対リンク [パス:行](相対パス#L開始-L終了) に変える。"""
    def repl(m):
        path, start, end = m.group(1), m.group(2), m.group(3) or m.group(2)
        rel = os.path.relpath(os.path.join(root, path), doc_dir).replace(os.sep, "/")
        return f"{m.group(0)}({rel}#L{start}-L{end})"
    pattern = re.compile(CITATION_RE.pattern + r"(?!\()")
    return pattern.sub(repl, text)


def _md_escape(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def _chapter(results: dict, cid: str) -> dict | None:
    return results.get(cid)


def _body(ch: dict | None) -> str:
    return ch["body"] if ch and ch.get("body") else _FAILED


def _module_table(facts: dict, module_summaries: dict, link_prefix: str | None) -> str:
    rows = ["| モジュール | 目的 | ファイル数 | 行数 | fan-in | fan-out | 不安定度 |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for mod, m in facts["modules"].items():
        purpose = (module_summaries.get(mod) or {}).get("summary", {}).get("purpose", "")
        name = f"[{mod}]({link_prefix}{module_slug(mod)}.md)" if link_prefix is not None else mod
        rows.append(f"| {name} | {_md_escape(purpose)} | {m['files']} | {m['loc']} | {m['fan_in']} | "
                    f"{m['fan_out']} | {m['instability']} |")
    return "\n".join(rows)


def _glossary(system: dict) -> str:
    items = system.get("glossary") or []
    if not items:
        return "（用語は抽出されませんでした）"
    rows = ["| 用語 | 定義 |", "|---|---|"]
    rows += [f"| {_md_escape(g.get('term'))} | {_md_escape(g.get('definition'))} |"
             for g in items if isinstance(g, dict)]
    return "\n".join(rows)


def _cycles_note(facts: dict) -> str:
    if not facts["cycles"]:
        return "モジュール間の循環依存は検出されませんでした。"
    return "**循環依存:** " + " / ".join(" ⇄ ".join(c) for c in facts["cycles"])


def render_overview(results, system, diagrams) -> str:
    parts = ["# 概要設計書（外部設計）", ""]
    n = 0
    for cid in ("overview-summary", "overview-features", "overview-usecases",
                "overview-structure", "overview-external"):
        ch = _chapter(results, cid)
        n += 1
        title = ch["title"] if ch else cid
        parts += [f"## {n}. {title}", ""]
        if cid == "overview-structure":
            parts += [fenced(diagrams.get("module_dependencies.mmd", "")), ""]
        parts += [_body(ch), ""]
    parts += [f"## {n + 1}. 用語集", "", _glossary(system), ""]
    return "\n".join(parts)


def render_detail(results, chapters, facts, module_summaries, diagrams, module_link_prefix) -> str:
    parts = ["# 詳細設計書（内部設計）", ""]
    n = 1
    arch = _chapter(results, "detail-architecture")
    parts += [f"## {n}. システム構成と設計方針", "",
              "### モジュール依存図（静的解析により生成）", "",
              fenced(diagrams.get("module_dependencies.mmd", "")), "", _cycles_note(facts), "",
              _body(arch), ""]
    n += 1
    parts += [f"## {n}. モジュール一覧", "", _module_table(facts, module_summaries, module_link_prefix), ""]
    for ch in chapters:
        if ch.doc != "detail" or ch.id == "detail-architecture":
            continue
        n += 1
        parts += [f"## {n}. {ch.title}", "", _body(_chapter(results, ch.id)), ""]
    return "\n".join(parts)


def render_module(graph: CodeGraph, module: str, result, module_summary, file_summaries, diagrams,
                  heading: str = "#") -> str:
    files = graph.modules().get(module, [])
    parts = [f"{heading} モジュール: {module}", ""]
    purpose = (module_summary or {}).get("summary", {}).get("purpose")
    if purpose:
        parts += [f"> {purpose}", ""]
    rows = ["| ファイル | 行数 | 目的 |", "|---|---:|---|"]
    for p in files:
        fs = (file_summaries.get(p) or {}).get("summary", {})
        rows.append(f"| {p} | {graph.files[p].loc} | {_md_escape(fs.get('purpose', ''))} |")
    parts += [f"{heading}# ファイル構成", "", "\n".join(rows), ""]
    diagram = diagrams.get(f"classes_{module_slug(module)}.mmd")
    if diagram:
        parts += [f"{heading}# クラス図（静的解析により生成）", "", fenced(diagram), ""]
    syms = [s for p in files for s in graph.files[p].symbols if s.exported]
    if syms:
        rows = ["| シンボル | 種別 | シグネチャ | 位置 |", "|---|---|---|---|"]
        for s in syms[:200]:
            rows.append(f"| {s.qualname} | {s.kind} | `{_md_escape(s.signature)[:120]}` | "
                        f"[{s.file}:{s.start_line}-{s.end_line}] |")
        if len(syms) > 200:
            rows.append(f"| … 他 {len(syms) - 200} 件 | | | |")
        parts += [f"{heading}# 公開シンボル一覧", "", "\n".join(rows), ""]
    parts += [f"{heading}# 解説", "", _body(result), ""]
    return "\n".join(parts)


def coverage(graph, file_summaries, module_summaries, results, pipe, system_out) -> dict:
    cite_valid = sum(r["citations"]["valid"] for r in results.values() if r)
    cite_invalid = sum(r["citations"]["invalid"] for r in results.values() if r)
    dropped = {}
    for key, r in list(file_summaries.items()) + list(module_summaries.items()) + list(results.items()):
        if r and r.get("dropped"):
            dropped[key] = r["dropped"]
    return {
        "files_total": len(graph.files),
        "files_summarized": len(file_summaries),
        "files_by_mode": {m: sum(1 for f in file_summaries.values() if f["mode"] == m)
                          for m in ("full", "skeleton", "split")},
        "modules_total": len(graph.modules()),
        "modules_summarized": len(module_summaries),
        "chapters_total": len(results),
        "chapters_failed": [k for k, v in results.items() if not v],
        "chapters_regenerated": [k for k, v in results.items() if v and v.get("regenerated")],
        "review_issues": {k: v["issues"] for k, v in results.items() if v and v.get("issues")},
        "citations": {"valid": cite_valid, "invalid": cite_invalid,
                      "valid_ratio": round(cite_valid / (cite_valid + cite_invalid), 3)
                      if cite_valid + cite_invalid else None},
        "dropped_context": dropped,
        "system_dropped": (system_out or {}).get("dropped", []),
        "failed_steps": [{"step": s, "error": e} for s, e in pipe.failures],
        "parse_errors": {p: pf.error for p, pf in graph.files.items() if pf.error},
    }


def render_readme(info: dict, cov: dict, has_adr: bool) -> str:
    rows = "\n".join(f"| {k} | {_md_escape(v)} |" for k, v in info.items())
    cites = cov["citations"]
    ratio = f"{cites['valid_ratio']:.1%}" if cites["valid_ratio"] is not None else "-"
    lines = [
        "# 設計書（ai_audit により自動生成）", "",
        "## 目次", "",
        "- [概要設計書](overview.md) — システムの目的・機能・構成（非エンジニア・再実装用）",
        "- [詳細設計書](detail.md) — 構成・処理フロー・データ・横断的関心事（保守担当者向け）",
        "- [モジュール別設計](modules/) — モジュールごとの構成とシンボル一覧",
        "- [図（Mermaid）](diagrams/) — 静的解析から生成した依存図・クラス図",
    ]
    if has_adr:
        lines.append("- [設計判断の記録（ADR）](adr/) — git 履歴から推定")
    lines += ["", "## 生成条件", "", "| 項目 | 値 |", "|---|---|", rows, "",
              "## カバレッジ", "",
              f"- ファイル要約: {cov['files_summarized']} / {cov['files_total']}"
              f"（全ソース {cov['files_by_mode']['full']}、スケルトン {cov['files_by_mode']['skeleton']}、"
              f"分割 {cov['files_by_mode']['split']}）",
              f"- モジュール要約: {cov['modules_summarized']} / {cov['modules_total']}",
              f"- 章: {cov['chapters_total'] - len(cov['chapters_failed'])} / {cov['chapters_total']}"
              f"（レビュー後に再生成: {len(cov['chapters_regenerated'])}）",
              f"- 根拠の有効率: {ratio}（有効 {cites['valid']} / 無効として除去 {cites['invalid']}）",
              "", "> 注意: 本書は LLM が生成した文書です。「（推測）」と付いた記述はコードから確定できない内容です。",
              "> 図・モジュール一覧・シンボル一覧は静的解析から機械的に生成しています。"]
    if cov["failed_steps"]:
        lines += ["", f"> ⚠️ {len(cov['failed_steps'])} 件のステップが失敗しました。"
                      "同じコマンドを再実行すると、失敗した部分だけを再試行します。"]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: str, layout: str, graph: CodeGraph, facts, chapters, results,
                  file_summaries, module_summaries, system, diagrams, info, cov, adrs) -> list[str]:
    root = graph.root
    written = []

    def write(rel: str, text: str):
        path = os.path.join(out_dir, rel)
        text = link_citations(text, root, os.path.dirname(path)) if path.endswith(".md") else text
        atomic_write(path, text)
        written.append(path)

    module_names = sorted(graph.modules())
    if layout == "legacy":
        detail = render_detail(results, chapters, facts, module_summaries, diagrams, None)
        mods = [render_module(graph, m, results.get(f"module-{m}"), module_summaries.get(m),
                              file_summaries, diagrams, heading="##") for m in module_names]
        header = f"**生成日時:** {info['生成日時']}  \n**対象:** `{info['対象ディレクトリ']}`\n\n---\n\n"
        write("_design_detail.md", detail.replace("\n", "\n" + header, 1) + "\n\n---\n\n" + "\n\n".join(mods))
        write("_design_overview.md", render_overview(results, system, diagrams).replace("\n", "\n" + header, 1))
        return written

    write("README.md", render_readme(info, cov, bool(adrs)))
    write("overview.md", render_overview(results, system, diagrams))
    write("detail.md", render_detail(results, chapters, facts, module_summaries, diagrams, "modules/"))
    for m in module_names:
        write(f"modules/{module_slug(m)}.md",
              render_module(graph, m, results.get(f"module-{m}"), module_summaries.get(m),
                            file_summaries, diagrams))
    for name, diagram in diagrams.items():
        write(f"diagrams/{name}", diagram + "\n")
    for name, text in (adrs or {}).items():
        write(f"adr/{name}", text)
    write("_meta/coverage.json", json.dumps(cov, ensure_ascii=False, indent=2))
    return written


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
