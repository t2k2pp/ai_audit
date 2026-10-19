import json

from ai_audit.usecases.audit import audit_directory, audit_file, units_for_file
from ai_audit.index.graph import CodeGraph


def _responder(verdict_valid=True):
    def respond(req):
        system = req["messages"][0]["content"]
        user = req["messages"][1]["content"]
        if "レビュー責任者" in system:
            return json.dumps({"verdicts": [{"index": 0, "valid": verdict_valid, "reason": "r"}]})
        if "セキュリティエンジニア" in system and "監査対象: shop/service.py::OrderRepository.save" in user:
            return json.dumps({"issues": [{"severity": "high", "line": 15, "title": "SQLi",
                                           "description": "SQL インジェクション", "suggestion": "プレースホルダ",
                                           "confidence": "high"}]})
        if "可読性" in system and "監査対象: shop/service.py::quick_order" in user:
            return json.dumps({"issues": [{"severity": "low", "line": 9999, "description": "範囲外の行"}]})
        return '{"issues": []}'
    return respond


def test_units_no_duplicates(sample_project):
    g = CodeGraph(str(sample_project))
    g.update()
    units = units_for_file(g, "shop/service.py", 200)
    ids = [u.id for u in units]
    assert len(ids) == len(set(ids))
    assert "shop/service.py::OrderRepository.save" in ids
    assert not any(u.kind == "class" for u in units)   # B2: クラス単位で重複監査しない


def test_audit_directory_with_context_and_verify(fake_llm, sample_project, tmp_path):
    fake_llm.responder = _responder()
    sarif = tmp_path / "out.sarif"
    summary = audit_directory(str(sample_project), sarif=str(sarif), quiet=True)
    assert summary["high_severity_total"] == 1
    report = json.loads((sample_project / "shop/service_audit.json").read_text(encoding="utf-8"))
    save = next(c for c in report["chunks"] if c["chunk_id"] == "shop/service.py::OrderRepository.save")
    issue = save["issues"][0]
    assert issue["line"] == 15 and issue["line_number_offset"] == 15 - save["start_line"]
    quick = next(c for c in report["chunks"] if c["name"] == "quick_order")
    assert quick["issues"][0]["line_approximate"] and quick["issues"][0]["line"] == quick["start_line"]
    # 文脈: security ウェアには呼び出し元（checkout）が渡る
    sec = [r for r in fake_llm.chat_requests() if "セキュリティエンジニア" in r["messages"][0]["content"]
           and "監査対象: shop/service.py::OrderRepository.save" in r["messages"][1]["content"]]
    assert "呼び出し元: shop/service.py::OrderService.checkout" in sec[0]["messages"][1]["content"]
    data = json.loads(sarif.read_text(encoding="utf-8"))
    assert data["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 15

    # キャッシュ: 再実行で LLM を呼ばない。モデル名が変われば再監査【B7】
    n = len(fake_llm.chat_requests())
    audit_directory(str(sample_project), quiet=True)
    assert len(fake_llm.chat_requests()) == n


def test_audit_cache_key_includes_model(fake_llm, sample_project, monkeypatch):
    fake_llm.responder = _responder()
    audit_file(str(sample_project / "shop/models.py"), root=str(sample_project), quiet=True)
    n = len(fake_llm.chat_requests())
    monkeypatch.setenv("LLM_MODEL_NAME", "other-model")
    audit_file(str(sample_project / "shop/models.py"), root=str(sample_project), quiet=True)
    assert len(fake_llm.chat_requests()) > n


def test_verifier_drops_invalid(fake_llm, sample_project):
    fake_llm.responder = _responder(verdict_valid=False)
    report = audit_file(str(sample_project / "shop/service.py"), root=str(sample_project), quiet=True)
    save = next(c for c in report["chunks"] if c["chunk_id"].endswith("OrderRepository.save"))
    assert save["issues"] == []


def test_compact_has_no_context_or_verify(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "4096")
    fake_llm.responder = _responder(verdict_valid=False)
    report = audit_file(str(sample_project / "shop/service.py"), root=str(sample_project), quiet=True)
    assert report["tier"] == "compact" and report["total_issues"] == 2  # 検証パスなし
    assert not any("呼び出し元" in r["messages"][1]["content"] for r in fake_llm.chat_requests())
