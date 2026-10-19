from ai_audit.usecases.architecture import detect_smells, review_architecture
from ai_audit.usecases.design_doc import generate_design_doc
from ai_audit.usecases.design_doc.facts import collect_facts, update_index
from responders import smart


def test_review_architecture_reuses_design_summaries(fake_llm, sample_project, tmp_path):
    fake_llm.responder = smart
    generate_design_doc(str(sample_project), quiet=True)
    n = len(fake_llm.chat_requests())
    out = tmp_path / "out" / "arch.md"
    report = review_architecture(str(sample_project), output_file=str(out), quiet=True)
    new = fake_llm.chat_requests()[n:]
    # ファイル要約・モジュール要約は再利用され、新規はレビュー 1 回だけ
    assert len(new) == 1 and "シニア・アーキテクト" in new[0]["messages"][0]["content"]
    assert "## 1. 静的解析の結果" in report and "| shop | 3 |" in report
    assert out.exists()


def test_review_architecture_compact_uses_findings(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "4096")
    for i in range(12):
        d = sample_project / f"pkg{i}"
        d.mkdir()
        (d / "mod.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    def responder(req):
        if "\"findings\"" in req["messages"][0]["content"]:
            return '{"findings": [{"perspective": "依存", "target": "shop", "problem": "p", "suggestion": "s", "priority": "high"}]}'
        return smart(req)

    fake_llm.responder = responder
    import ai_audit.usecases.design_doc.summarize as summ
    big = {"purpose": "x" * 3000, "responsibilities": ["y" * 500]}
    monkeypatch.setattr(summ, "reduce_json", _wrap(summ.reduce_json, big))
    report = review_architecture(str(sample_project), quiet=True)
    assert "## 2. レビュー" in report
    assert any("\"findings\"" in r["messages"][0]["content"] for r in fake_llm.chat_requests())


def _wrap(fn, big):
    def inner(*args, **kwargs):
        out = fn(*args, **kwargs)
        out["result"] = dict(out["result"], **big)  # モジュール要約を大きくして入りきらない状況を作る
        return out
    return inner


def test_detect_smells(sample_project):
    (sample_project / "shop" / "models.py").write_text(
        "from .service import OrderService\nclass Item:\n    pass\n", encoding="utf-8")
    g = update_index(str(sample_project), log=lambda *_: None)
    smells = detect_smells(g, collect_facts(g))
    assert any("ファイルの循環 import" in s for s in smells)
