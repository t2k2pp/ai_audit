"""
ユースケースC v2: アーキテクチャ解析

- 依存関係・循環・メトリクス・兆候（巨大ファイル・巨大クラス・依存の集中）は機械的に算出する
- LLM はモジュール要約（設計書生成と共有・再利用）と事実をもとに評価と改善計画を書く
- 全モジュールが 1 回の入力に収まらない場合は、指摘を部分ごとに抽出して統合してから書く
"""
import os

from ..core.runtime import Runtime, create_runtime
from ..pipeline.engine import Pipeline, StepFailed
from ..pipeline.jobstore import JobStore, work_base
from ..pipeline.llm_steps import compact_json, new_builder, reduce_json, text_step
from ..render.mermaid import fenced, module_dependency_graph
from ..wears import architecture as A
from ..wears import design as W
from .design_doc.assemble import link_citations, now_text
from .design_doc.facts import collect_facts, facts_text, update_index
from .design_doc.sections import check_citations
from .design_doc.summarize import run_s2, run_s3

LARGE_FILE_LOC = 500
LARGE_CLASS_METHODS = 20


def detect_smells(graph, facts) -> list[str]:
    """機械的に検出できる構造上の兆候。"""
    smells = []
    for cyc in facts["cycles"]:
        smells.append(f"モジュールの循環依存: {' ⇄ '.join(cyc)}")
    file_cycles = graph.cycles({k: set(v) for k, v in graph.file_deps.items()})
    for cyc in file_cycles[:10]:
        smells.append(f"ファイルの循環 import: {' ⇄ '.join(cyc)}")
    for path, pf in sorted(graph.files.items(), key=lambda x: -x[1].loc):
        if pf.loc >= LARGE_FILE_LOC:
            smells.append(f"巨大ファイル: {path}（{pf.loc} 行）")
    for sym in graph.symbols.values():
        if sym.kind == "class":
            n = len(graph.children(sym.id))
            if n >= LARGE_CLASS_METHODS:
                smells.append(f"メソッドの多いクラス: {sym.qualname}（{n} メソッド）[{sym.file}:{sym.start_line}-{sym.end_line}]")
    metrics = facts["modules"]
    for mod, m in metrics.items():
        if m["fan_in"] >= 3 and m["instability"] >= 0.7:
            smells.append(f"多くのモジュールから依存されているのに不安定なモジュール: {mod}"
                          f"（fan-in {m['fan_in']}, 不安定度 {m['instability']}）")
        if m["fan_out"] >= 6:
            smells.append(f"依存先が多いモジュール: {mod}（fan-out {m['fan_out']}）")
    return smells


def _metrics_table(facts) -> str:
    rows = ["| モジュール | ファイル | 行数 | シンボル | fan-in | fan-out | 不安定度 |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for mod, m in facts["modules"].items():
        rows.append(f"| {mod} | {m['files']} | {m['loc']} | {m['symbols']} | {m['fan_in']} | "
                    f"{m['fan_out']} | {m['instability']} |")
    return "\n".join(rows)


def review_architecture(directory: str, output_file: str | None = None, force: bool = False,
                        tier: str | None = None, runtime: Runtime | None = None,
                        quiet: bool = False, lang: str = "ja") -> str:
    abs_dir = os.path.abspath(directory)
    log = (lambda *a, **k: None) if quiet else print
    rt = runtime or create_runtime(tier=tier, quiet=quiet)
    graph = update_index(abs_dir, log=log)
    if not graph.files:
        return "# アーキテクチャレビュー\n\n対象のソースファイルが見つかりませんでした。\n"
    facts = collect_facts(graph)
    smells = detect_smells(graph, facts)
    # S2/S3 は設計書生成と同じプロンプト版数で実行し、成果物を共有する
    pipe = Pipeline("architecture", JobStore(work_base(abs_dir), "architecture"), rt, force=force,
                    version=W.PROMPT_VERSION, quiet=quiet, lang=lang)
    file_summaries = run_s2(pipe, rt, graph)
    module_summaries = run_s3(pipe, rt, graph, facts, file_summaries)

    pipe.stage("アーキテクチャレビュー")
    system = W.with_rules(A.ARCH_REVIEWER, rt.tier, lang)
    smell_text = "\n".join(f"- {s}" for s in smells) or "（機械的に検出した兆候はありません）"
    facts_alts = [facts_text(facts), facts_text(facts, include_readme=False)]
    items = []
    for mod, ms in sorted(module_summaries.items()):
        s = ms["summary"]
        items.append((mod, [compact_json(s), compact_json({k: s.get(k) for k in (
            "purpose", "responsibilities", "concerns")})]))

    b = new_builder(rt, system)
    b.add("facts", "静的解析の事実", facts_alts, required=True)
    b.add("smells", "機械的に検出した兆候", smell_text, required=True)
    for key, alts in items:
        b.add(f"module:{key}", f"モジュール要約: {key}", alts, priority=20)
    try:
        if b.build(strict=False).dropped:
            # 全モジュールが入らない: 部分ごとに指摘を抽出して統合してから書く
            fsys = W.with_rules(A.ARCH_FINDINGS, rt.tier, lang)
            fmerge = W.with_rules(A.ARCH_FINDINGS_MERGE, rt.tier, lang)
            found = reduce_json(pipe, rt, "arch/findings", fsys, fmerge,
                                [("facts", "静的解析の事実", facts_alts[1:])], items,
                                A.ARCH_FINDINGS_SCHEMA, item_title="モジュール要約")
            b = new_builder(rt, system)
            b.add("facts", "静的解析の事実", facts_alts, required=True)
            b.add("smells", "機械的に検出した兆候", smell_text, required=True)
            b.add("findings", "部分ごとに抽出した指摘", compact_json(found["result"]), required=True)
        body = text_step(pipe, rt, "arch/review", system, b)["result"]
    except StepFailed:
        body = "*レビューの生成に失敗しました。もう一度実行すると続きから処理します。*"
    body, _ = check_citations(body, graph)
    pipe.finish()
    rt.close()

    report = "\n".join([
        "# アーキテクチャレビューレポート", "",
        f"- 対象ディレクトリ: `{abs_dir}`",
        f"- 生成日時: {now_text()}",
        f"- モデル / ティア: {rt.settings.model_name} / {rt.tier}",
        f"- 規模: {facts['stats']['files']} ファイル / {facts['stats']['loc']} 行 / {len(facts['modules'])} モジュール",
        "", "## 1. 静的解析の結果（機械算出）", "",
        "### モジュール依存図", "", fenced(module_dependency_graph(
            facts["module_deps"], list(facts["modules"]), facts["cycles"])), "",
        "### メトリクス", "", _metrics_table(facts), "",
        "### 検出した兆候", "", smell_text, "",
        "## 2. レビュー", "", body.strip(), "",
    ])
    if output_file:
        out_path = os.path.abspath(output_file)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        report = link_citations(report, abs_dir, os.path.dirname(out_path))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        log(f"[INFO] レポートを保存しました: {out_path}")
    return report
