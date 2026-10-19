from ai_audit.usecases.design_doc import generate_design_doc
from responders import smart


def _file_requests(fake_llm):
    return [r["messages"][1]["content"] for r in fake_llm.chat_requests()
            if "1 つのソースファイル" in r["messages"][0]["content"]]


def test_large_tier_adds_related_context(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "262144")
    fake_llm.responder = smart
    generate_design_doc(str(sample_project), adr=False, quiet=True)
    service = next(t for t in _file_requests(fake_llm) if "パス: shop/service.py" in t)
    assert "関連ファイルのスケルトン: shop/models.py" in service


def test_standard_tier_has_no_related_context(fake_llm, sample_project):
    fake_llm.responder = smart
    generate_design_doc(str(sample_project), quiet=True)
    assert not any("関連ファイルのスケルトン" in t for t in _file_requests(fake_llm))


def test_compact_tier_degrades_large_file(fake_llm, sample_project, monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "4096")
    big = "\n\n".join(f"def func_{i}(x):\n" + "\n".join(f"    x = x + {j}  # 計算 {j}" for j in range(30))
                      + "\n    return x" for i in range(30))
    (sample_project / "shop" / "big.py").write_text(big, encoding="utf-8")
    fake_llm.responder = smart
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["ok"]
    modes = out["coverage"]["files_by_mode"]
    assert modes["split"] + modes["skeleton"] >= 1


def test_compact_requests_stay_within_4096(fake_llm, sample_project, monkeypatch):
    """
    シンボルが多い大きなファイルでも、すべてのリクエストが 4096 トークンに収まる。
    トークン数はサーバ側の数え方（ダミーは 4 文字 = 1 トークン。usage で補正係数が学習される）で確認する。
    """
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "4096")
    funcs = "\n\n".join(f"def handler_{i}(request, user_id: int, options: dict | None = None) -> dict:\n"
                        f"    data = load(request, user_id)\n    return transform_{i}(data, options)"
                        for i in range(250))
    (sample_project / "shop" / "handlers.py").write_text(funcs, encoding="utf-8")
    fake_llm.responder = smart
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["ok"]
    for r in fake_llm.chat_requests():
        server_tokens = sum(len(m["content"]) for m in r["messages"]) // 4
        assert server_tokens + r.get("max_tokens", 0) <= 4096, r["messages"][1]["content"][:100]
