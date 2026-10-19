"""
ユースケースD v2: 設計書生成（根拠付き・階層統合・中断再開）

S0 インデックス → S1 事実 → S2 ファイル要約 → S3 モジュール要約 → S4 システム要約
→ S5 章の生成 → S6 検証 → S7 組み立て（T3 では ADR も生成）

各ステップの成果物は <対象>/.ai_audit/work/design_doc/ に保存され、
再実行時は入力が変わっていないステップを再利用する（--force で全再生成）。
"""
import os
import sys
from dataclasses import asdict

from ...core.budget import TIER_LARGE
from ...core.runtime import Runtime, create_runtime
from ...index.gitinfo import is_git_repo
from ...pipeline.engine import Pipeline, StepFailed
from ...pipeline.jobstore import JobStore, work_base
from ...wears import design as W
from .assemble import coverage, now_text, write_outputs
from .facts import build_diagrams, collect_facts, update_index
from .sections import plan_chapters, write_chapter
from .summarize import run_s2, run_s3, run_s4

WORK_NAME = "design_doc"


def generate_design_doc(directory: str, output_dir: str | None = None, force: bool = False,
                        tier: str | None = None, layout: str = "v2", lang: str = "ja",
                        verify: bool = True, adr: bool | None = None,
                        runtime: Runtime | None = None, quiet: bool = False) -> dict:
    """
    設計書を生成する。戻り値: {"output_dir", "files", "coverage", "ok"}
    """
    abs_dir = os.path.abspath(directory)
    if not os.path.isdir(abs_dir):
        raise FileNotFoundError(f"ディレクトリが見つかりません: {abs_dir}")
    if layout == "legacy":
        out_dir = os.path.abspath(output_dir) if output_dir else abs_dir
    else:
        out_dir = os.path.abspath(output_dir) if output_dir else os.path.join(abs_dir, "_design")
    log = (lambda *a, **k: None) if quiet else print

    rt = runtime or create_runtime(tier=tier, quiet=quiet)
    if tier and rt.settings.tier != tier:
        rt.settings.tier = tier
    graph = update_index(abs_dir, log=log)
    facts = collect_facts(graph)
    pipe = Pipeline(WORK_NAME, JobStore(work_base(abs_dir), WORK_NAME), rt, force=force,
                    version=W.PROMPT_VERSION, quiet=quiet, lang=lang)
    pipe.stage("S1 事実の抽出")
    diagrams = build_diagrams(graph, facts)
    log(f"[INFO] {facts['stats']['files']} ファイル / {len(facts['modules'])} モジュール / "
        f"エントリポイント {len(facts['entry_points'])} 件")

    if not graph.files:
        raise ValueError("解析可能なソースファイルが見つかりませんでした。")

    file_summaries = run_s2(pipe, rt, graph)
    module_summaries = run_s3(pipe, rt, graph, facts, file_summaries)
    try:
        system_out = run_s4(pipe, rt, facts, module_summaries)
    except StepFailed:
        pipe.finish()
        rt.close()
        print("[ERROR] システム要約の生成に失敗したため中断しました。再実行すると続きから処理します。",
              file=sys.stderr)
        return {"output_dir": out_dir, "files": [], "coverage": None, "ok": False}
    system = system_out["summary"]

    chapters = plan_chapters(system, sorted(graph.modules()))
    pipe.stage("S5/S6 章の生成と検証", len(chapters))
    chapter_results = pipe.map(
        chapters,
        name_fn=lambda c: f"s5-chapter/{c.id}",
        inputs_fn=lambda c: {
            "chapter": asdict(c), "system": system,
            "modules": {m: module_summaries.get(m, {}).get("summary") for m in c.modules},
            "files": {p: file_summaries.get(p, {}).get("summary")
                      for m in c.modules for p in graph.modules().get(m, [])} if c.doc != "overview" else {},
            "shas": {p: graph.files[p].sha256 for m in c.modules for p in graph.modules().get(m, [])},
            "verify": verify, "prompt": W.PROMPT_VERSION, "budget": rt.budget.input_budget, "lang": lang,
        },
        fn=lambda c: write_chapter(pipe, rt, c, graph, facts, file_summaries, module_summaries,
                                   system, verify),
        label_fn=lambda c: c.title,
    )
    results = {c.id: r for c, r in zip(chapters, chapter_results)}

    adrs = {}
    want_adr = adr if adr is not None else (rt.tier == TIER_LARGE and is_git_repo(abs_dir))
    if want_adr and layout != "legacy":
        from .adr import run_adr
        adrs = run_adr(pipe, rt, abs_dir, system)

    pipe.stage("S7 組み立て")
    cov = coverage(graph, file_summaries, module_summaries, results, pipe, system_out)
    git = facts["project"]["git"]
    info = {
        "生成日時": now_text(),
        "対象ディレクトリ": abs_dir,
        "対象コミット": (f"{git.get('commit')} ({git.get('branch')})" + ("・未コミットの変更あり" if git.get("dirty") else ""))
        if git else "git 管理外",
        "モデル": rt.settings.model_name,
        "サーバ": rt.profile.server_type,
        "ティア": rt.tier,
        "実効予算 / 入力予算": f"{rt.budget.effective:,} / {rt.budget.input_budget:,} トークン",
        "LLM 呼び出し": f"{rt.usage.calls} 回（入力 {rt.usage.prompt_tokens:,} / 出力 {rt.usage.completion_tokens:,} トークン）",
        "整合性レビュー": "あり" if verify and rt.tier != "compact" else "なし",
    }
    files = write_outputs(out_dir, layout, graph, facts, chapters, results, file_summaries,
                          module_summaries, system, diagrams, info, cov, adrs)
    pipe.finish()
    rt.close()
    log(f"[INFO] 出力先: {out_dir}（{len(files)} ファイル）")
    return {"output_dir": out_dir, "files": files, "coverage": cov, "ok": not pipe.failures}
