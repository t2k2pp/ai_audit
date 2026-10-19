from ai_audit.core.config import load_settings
from ai_audit.core.server_probe import FALLBACK_CONTEXT, probe_server


def test_probe_llamacpp(fake_llm):
    fake_llm.n_ctx = 65536
    p = probe_server(load_settings(), use_cache=False)
    assert (p.server_type, p.context_length, p.tokenize) == ("llamacpp", 65536, "llamacpp")


def test_probe_ollama_warns(fake_llm):
    fake_llm.mode = "ollama"
    fake_llm.n_ctx = 262144
    p = probe_server(load_settings(), use_cache=False)
    assert p.server_type == "ollama" and p.context_length == 262144
    assert any("num_ctx" in w for w in p.warnings)


def test_probe_fallback_and_env_override(fake_llm, monkeypatch):
    fake_llm.mode = "plain"
    p = probe_server(load_settings(), use_cache=False)
    assert p.context_length == FALLBACK_CONTEXT and p.context_source == "fallback"
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "131072")
    p = probe_server(load_settings())  # キャッシュ経由でも env が優先される
    assert p.context_length == 131072 and p.context_source == "env"
    assert not any("LLM_CONTEXT_LENGTH" in w for w in p.warnings)
