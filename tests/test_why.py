import json

from ai_audit.usecases.why import extract_why, list_why, search_why


def _responder(req):
    user = req["messages"][1]["content"]
    if "OrderRepository.save（" in user.split("\n")[0]:
        return "注文をデータベースに保存する。SQL を文字列で組み立てている（推測: 速度優先）。"
    return "単純さを優先した実装（推測）。"


def test_extract_search_and_incremental(fake_llm, sample_project, monkeypatch):
    fake_llm.responder = _responder
    stats = extract_why(str(sample_project), quiet=True)
    assert stats["extracted"] > 10 and stats["failed"] == 0
    hits = search_why("データベース 保存", directory=str(sample_project))
    assert hits[0]["function_name"] == "OrderRepository.save"
    data = json.loads((sample_project / ".ai_audit/why.json").read_text(encoding="utf-8"))
    assert any(e["qualname"] == "OrderService.checkout" for e in data["shop/service.py"])

    # 変更なし → 全スキップ。1 関数変更 → その関数だけ再抽出
    assert extract_why(str(sample_project), quiet=True)["extracted"] == 0
    p = sample_project / "web/util.js"
    p.write_text(p.read_text(encoding="utf-8").replace("円", " JPY"), encoding="utf-8")
    assert extract_why(str(sample_project), quiet=True)["extracted"] == 1
    assert len(list_why(str(sample_project))) == stats["extracted"]


def test_embeddings_used_when_configured(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "emb")
    fake_llm.responder = _responder
    extract_why(str(sample_project), quiet=True)
    assert any(r.get("_path") == "/v1/embeddings" for r in fake_llm.requests)
    assert search_why("保存", directory=str(sample_project))
