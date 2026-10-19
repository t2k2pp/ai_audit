import json

from ai_audit.usecases.design_doc import generate_design_doc
from responders import smart


def test_design_doc_standard_and_resume(fake_llm, sample_project):
    fake_llm.responder = smart
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["ok"]
    d = sample_project / "_design"
    for name in ("README.md", "overview.md", "detail.md", "modules/shop.md",
                 "diagrams/module_dependencies.mmd", "_meta/coverage.json"):
        assert (d / name).exists(), name
    detail = (d / "detail.md").read_text(encoding="utf-8")
    assert "```mermaid\ngraph LR" in detail and "処理フロー: 注文確定" in detail
    # 根拠チェック: 有効な根拠はリンク化、無効な根拠は除去
    assert "[shop/service.py:19-27](../shop/service.py#L19-L27)" in detail
    assert "shop/nothing.py" not in detail and "900-901" not in detail
    module_page = (d / "modules/shop.md").read_text(encoding="utf-8")
    assert "classDiagram" in module_page and "OrderService.checkout" in module_page
    cov = json.loads((d / "_meta/coverage.json").read_text(encoding="utf-8"))
    assert cov["files_summarized"] == cov["files_total"] == 7
    assert cov["citations"]["invalid"] > 0 and cov["citations"]["valid"] > 0
    # 存在しない key_symbol は除去され、行番号は正確な値に置き換わる
    assert "standard" in (d / "README.md").read_text(encoding="utf-8")

    # 再実行: すべて再利用され LLM は呼ばれない
    n = len(fake_llm.chat_requests())
    generate_design_doc(str(sample_project), quiet=True)
    assert len(fake_llm.chat_requests()) == n

    # 1 ファイル変更: そのファイルの要約と、影響する上位ステップだけ再実行
    p = sample_project / "web/util.js"
    p.write_text(p.read_text(encoding="utf-8") + "\nexport const x = () => 1;\n", encoding="utf-8")
    generate_design_doc(str(sample_project), quiet=True)
    new_reqs = fake_llm.chat_requests()[n:]
    file_reqs = [r for r in new_reqs if "1 つのソースファイル" in r["messages"][0]["content"]]
    assert len(file_reqs) == 1 and "web/util.js" in file_reqs[0]["messages"][1]["content"]


def test_design_doc_compact_4096(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "4096")
    fake_llm.responder = smart
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["ok"]
    reqs = fake_llm.chat_requests()
    # T1 では整合性レビューを行わない
    assert not any("設計書レビュアー" in r["messages"][0]["content"] for r in reqs)
    # すべてのリクエストが 4096 の入力予算内（ダミーは 4 文字 = 1 トークン換算の usage を返す）
    from ai_audit.core.budget import estimate_tokens
    for r in reqs:
        text = "".join(m["content"] for m in r["messages"])
        assert estimate_tokens(text) <= 4096
    assert "compact" in (sample_project / "_design/README.md").read_text(encoding="utf-8")


def test_design_doc_review_regenerates(fake_llm, sample_project):
    issues = [{"severity": "high", "quote": "x", "problem": "事実と矛盾", "fix": "直す"}]
    fake_llm.responder = lambda req: smart(req, review_issues=issues)
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["coverage"]["chapters_regenerated"]
    regen = [r for r in fake_llm.chat_requests() if "前回のレビュー指摘" in r["messages"][1]["content"]]
    assert regen


def test_design_doc_legacy_layout(fake_llm, sample_project):
    fake_llm.responder = smart
    generate_design_doc(str(sample_project), layout="legacy", quiet=True)
    assert (sample_project / "_design_detail.md").exists()
    assert (sample_project / "_design_overview.md").exists()


def test_design_doc_failure_then_resume(fake_llm, sample_project):
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        if calls["n"] == 3:
            return "壊れた出力"   # JSON 不正 → 再生成も不正にするため 2 回続けて壊す
        if calls["n"] == 4:
            return "壊れた出力"
        return smart(req)

    fake_llm.responder = flaky
    out = generate_design_doc(str(sample_project), quiet=True)
    assert not out["ok"] and out["coverage"]["failed_steps"]
    assert out["coverage"]["files_summarized"] == 6
    fake_llm.responder = smart
    out2 = generate_design_doc(str(sample_project), quiet=True)
    assert out2["ok"] and out2["coverage"]["files_summarized"] == 7
