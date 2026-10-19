"""
S5: 章ごとの生成 / S6: 検証（根拠チェック・整合性レビュー）

章のコンテキストは次の優先順位で組み立てる（設計書 5.4 節）:
  1. 章の指示（必須） 2. システム要約（必須） 3. 関連モジュール要約 4. 事実
  5. 関連ファイル要約 6. 関連ソース（入らなければスケルトン）
"""
import re
from dataclasses import dataclass, field

from ...context.builder import number_lines
from ...core.budget import TIER_COMPACT, TIER_LARGE
from ...index.graph import CodeGraph
from ...pipeline.engine import StepFailed
from ...pipeline.llm_steps import compact_json, json_step, new_builder, text_step
from ...wears import design as W
from .facts import facts_text

CITATION_RE = re.compile(r"\[([\w.\-/@+]+\.(?:py|jsx?|tsx?|mjs|cjs|dart)):(\d+)(?:\s*-\s*(\d+))?\]")
MAX_FLOWS = 8


@dataclass
class Chapter:
    id: str
    doc: str                    # detail / overview / module
    title: str
    goal: str
    modules: list[str] = field(default_factory=list)
    extra: str = ""             # 章固有の追加情報（フロー定義など）
    writer: str = "section"     # section / overview


def plan_chapters(system: dict, module_names: list[str]) -> list[Chapter]:
    """目次（テンプレート＋システム要約で特定した主要フロー）。"""
    all_mods = list(module_names)
    flows = (system.get("key_flows") or [])[:MAX_FLOWS]
    chapters = [
        Chapter("detail-architecture", "detail", "システム構成と設計方針",
                "全体のアーキテクチャ、層・コンポーネントの構成、依存の方向、エントリポイント、設計方針を説明する。",
                all_mods),
    ]
    for i, flow in enumerate(flows, 1):
        mods = [m for m in flow.get("modules") or [] if m in module_names] or all_mods
        chapters.append(Chapter(
            f"detail-flow-{i}", "detail", f"処理フロー: {flow.get('name', f'フロー{i}')}",
            "この処理の流れを、関係するモジュール・クラス・関数の呼び出し順に説明し、"
            "Mermaid の sequenceDiagram を 1 つ含める。", mods, extra=compact_json(flow)))
    chapters += [
        Chapter("detail-data", "detail", "データモデル",
                "主要なデータ構造・型・永続化されるデータとその関係を説明する。", all_mods,
                extra=compact_json(system.get("data_entities") or [])),
        Chapter("detail-interfaces", "detail", "外部インターフェース",
                "エントリポイント（CLI・API・画面など）と、外部から使われる公開インターフェースを説明する。", all_mods),
        Chapter("detail-crosscutting", "detail", "エラーハンドリングと横断的関心事",
                "エラー処理、設定、ログ、永続化、セキュリティなどの方針と実装箇所を説明する。", all_mods,
                extra=compact_json(system.get("cross_cutting") or {})),
        Chapter("detail-concerns", "detail", "設計上の課題と改善の方向性",
                "循環依存・責務の集中・技術的負債などの課題と、段階的な改善の方向性を示す。", all_mods,
                extra=compact_json(system.get("concerns") or [])),
    ]
    for m in module_names:
        chapters.append(Chapter(
            f"module-{m}", "module", f"モジュール: {m}",
            "このモジュールの責務、構成要素、主要なクラス・関数の役割と使い方、他モジュールとの関係、"
            "改修時の注意点を説明する。", [m]))
    chapters += [
        Chapter("overview-summary", "overview", "システム概要",
                "システムが何をするものか、解決する課題、想定利用者を説明する。", all_mods, writer="overview"),
        Chapter("overview-features", "overview", "主要機能",
                "利用者から見た機能を一覧と説明で示す。", all_mods, writer="overview",
                extra=compact_json(system.get("features") or [])),
        Chapter("overview-usecases", "overview", "主要ユースケース",
                "代表的な利用シナリオを 2〜5 個、手順として示す。", all_mods, writer="overview",
                extra=compact_json(flows)),
        Chapter("overview-structure", "overview", "システム構成と主要コンポーネント",
                "システムを構成する主要コンポーネントの役割を、非技術者にも伝わる言葉で説明する。",
                all_mods, writer="overview", extra=compact_json(system.get("layers") or [])),
        Chapter("overview-external", "overview", "外部連携とデータ",
                "連携する外部システム・ライブラリと、扱う主要なデータを説明する。", all_mods,
                writer="overview", extra=compact_json({"external": system.get("external_systems") or [],
                                                       "data": system.get("data_entities") or []})),
    ]
    return chapters


# ---------------------------------------------------------------------------
# コンテキスト組み立て
# ---------------------------------------------------------------------------
def _module_source_parts(graph: CodeGraph, file_summaries: dict, modules: list[str], tier: str,
                         limit_files: int = 12) -> list[tuple[str, list[str]]]:
    """関連ソース: モジュール内の主要シンボルのソース（入らなければスケルトン）。"""
    parts = []
    mod_files = graph.modules()
    for m in modules:
        for path in mod_files.get(m, [])[:limit_files]:
            pf = graph.files[path]
            skeleton = pf.skeleton or ""
            if tier == TIER_COMPACT:
                parts.append((path, [skeleton]))
                continue
            source = graph.source_of(path)
            parts.append((path, [number_lines(source), skeleton]))
    return parts


def build_chapter_context(rt, system_prompt: str, ch: Chapter, graph: CodeGraph, facts: dict,
                          file_summaries: dict, module_summaries: dict, system_summary: dict,
                          feedback: str = ""):
    b = new_builder(rt, system_prompt)
    b.add("chapter", "執筆する章",
          f"章タイトル: {ch.title}\n目的: {ch.goal}" + (f"\n章の対象: {ch.extra}" if ch.extra else ""),
          required=True)
    full_sys = compact_json(system_summary)
    small_sys = compact_json({k: system_summary.get(k) for k in ("name", "overview", "architecture")})
    b.add("system", "システム要約", [full_sys, small_sys], required=True)
    if feedback:
        b.add("feedback", "前回のレビュー指摘（必ず反映すること）", feedback, required=True)
    for m in ch.modules:
        ms = module_summaries.get(m)
        if ms:
            s = ms["summary"]
            b.add(f"module:{m}", f"モジュール要約: {m}",
                  [compact_json(s), compact_json({"purpose": s.get("purpose"),
                                                  "responsibilities": s.get("responsibilities")})],
                  priority=20)
    b.add("facts", "静的解析の事実", [facts_text(facts, include_readme=ch.doc == "overview"),
                                     facts_text(facts, include_readme=False)], priority=30)
    if ch.doc != "overview":
        for m in ch.modules:
            for path in graph.modules().get(m, []):
                fs = file_summaries.get(path)
                if fs:
                    b.add(f"file:{path}", f"ファイル要約: {path}", compact_json(fs["summary"]), priority=40)
        if ch.id == "detail-interfaces":
            for path in facts["entry_points"][:5]:
                b.add(f"entry:{path}", f"エントリポイント: {path}",
                      [number_lines(graph.source_of(path)), graph.files[path].skeleton], priority=35)
        prio = 45 if ch.doc == "module" else 50
        for path, alts in _module_source_parts(graph, file_summaries, ch.modules, rt.tier,
                                               limit_files=50 if rt.tier == TIER_LARGE else 12):
            b.add(f"source:{path}", f"ソース: {path}", alts, priority=prio)
    return b


# ---------------------------------------------------------------------------
# S6: 検証
# ---------------------------------------------------------------------------
def check_citations(text: str, graph: CodeGraph) -> tuple[str, dict]:
    """
    根拠 [パス:行] の存在確認。存在しないファイル・範囲外の行は根拠表記を外し、件数を返す。
    """
    stats = {"valid": 0, "invalid": 0, "invalid_refs": []}

    def repl(m):
        path, start = m.group(1), int(m.group(2))
        end = int(m.group(3) or start)
        pf = graph.files.get(path)
        if pf and 1 <= start <= end <= pf.loc + 1:
            stats["valid"] += 1
            return m.group(0)
        stats["invalid"] += 1
        stats["invalid_refs"].append(m.group(0))
        return ""

    return CITATION_RE.sub(repl, text), stats


def review_chapter(pipe, rt, ch: Chapter, text: str, facts: dict, module_summaries: dict) -> list[dict]:
    system = W.with_rules(W.CONSISTENCY_REVIEWER, rt.tier, pipe.lang)
    b = new_builder(rt, system)
    b.add("chapter", f"レビュー対象の章: {ch.title}", text, required=True)
    b.add("facts", "静的解析の事実", [facts_text(facts, include_readme=False)], required=True)
    for m in ch.modules:
        ms = module_summaries.get(m)
        if ms:
            b.add(f"module:{m}", f"モジュール要約: {m}", compact_json(ms["summary"]), priority=20)
    out = json_step(pipe, rt, f"s6/{ch.id}", system, b, W.CONSISTENCY_SCHEMA)
    return [i for i in out["result"].get("issues", []) if isinstance(i, dict)]


def write_chapter(pipe, rt, ch: Chapter, graph: CodeGraph, facts: dict, file_summaries: dict,
                  module_summaries: dict, system_summary: dict, verify: bool) -> dict:
    """S5＋S6。戻り値: {"id", "title", "body", "citations", "issues", "dropped", "regenerated"}"""
    base = W.OVERVIEW_WRITER if ch.writer == "overview" else W.SECTION_WRITER
    system = W.with_rules(base, rt.tier, pipe.lang)
    ctx = build_chapter_context(rt, system, ch, graph, facts, file_summaries, module_summaries, system_summary)
    out = text_step(pipe, rt, f"s5/{ch.id}", system, ctx)
    text, regenerated, issues = out["result"], False, []

    if verify and rt.tier != TIER_COMPACT:
        try:
            issues = review_chapter(pipe, rt, ch, text, facts, module_summaries)
        except StepFailed:
            issues = []
        serious = [i for i in issues if str(i.get("severity", "")).lower() == "high"]
        if serious:
            feedback = "\n".join(f"- {i.get('quote', '')}: {i.get('problem', '')} → {i.get('fix', '')}"
                                 for i in serious)
            ctx2 = build_chapter_context(rt, system, ch, graph, facts, file_summaries, module_summaries,
                                         system_summary, feedback=feedback)
            out2 = text_step(pipe, rt, f"s5/{ch.id}/r1", system, ctx2)
            text, regenerated = out2["result"], True

    text, cite = check_citations(text, graph)
    return {"id": ch.id, "doc": ch.doc, "title": ch.title, "body": text.strip(), "citations": cite,
            "issues": issues, "dropped": out["dropped"], "degraded": out["degraded"],
            "regenerated": regenerated, "truncated": out["truncated"]}
