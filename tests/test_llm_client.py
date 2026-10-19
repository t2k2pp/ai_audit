import json

import pytest

from ai_audit.core.config import load_settings
from ai_audit.core.jsonutil import extract_json, strip_thinking, validate
from ai_audit.core.llm_client import ContextOverflowError, LLMClient, LLMOutputError
from ai_audit.core.runtime import create_runtime

SCHEMA = {"type": "object", "required": ["issues"],
          "properties": {"issues": {"type": "array", "items": {"type": "object", "required": ["line"]}}}}


def _client():
    c = LLMClient(load_settings())
    c.retry_delay = 0
    return c


def test_extract_json_variants():
    assert extract_json('説明\n```json\n{"a": 1,}\n```') == {"a": 1}
    assert extract_json('<think>考え中 {"x":0}</think>{"b": [1, 2]}') == {"b": [1, 2]}
    assert strip_thinking("abc</think> 本文") == "本文"
    with pytest.raises(ValueError):
        extract_json("no json")


def test_validate():
    assert validate({"issues": [{"line": 1}]}, SCHEMA) == []
    assert validate({"issues": [{}]}, SCHEMA)
    assert validate({"x": 1}, {"type": "object", "properties": {"x": {"type": "string"}}})


def test_chat_strips_reasoning_and_retries(fake_llm):
    fake_llm.fail_next = 1
    fake_llm.responder = lambda req: "<think>内部</think>答え"
    resp = _client().chat("sys", "user")
    assert resp.text == "答え"
    assert len(fake_llm.chat_requests()) == 2  # 1 回目は 500、2 回目で成功


def test_chat_json_schema_and_repair(fake_llm):
    answers = iter(["壊れた出力", json.dumps({"issues": [{"line": 3}]})])
    fake_llm.responder = lambda req: next(answers)
    resp = _client().chat_json("sys", "user", schema=SCHEMA)
    assert resp.data == {"issues": [{"line": 3}]}
    reqs = fake_llm.chat_requests()
    assert reqs[0]["response_format"]["type"] == "json_schema"
    assert "前回の出力は" in reqs[1]["messages"][1]["content"]


def test_chat_json_gives_up(fake_llm):
    fake_llm.responder = lambda req: "{}"
    with pytest.raises(LLMOutputError):
        _client().chat_json("sys", "user", schema=SCHEMA)


def test_extra_body_and_reasoning(fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"top_k": 20}')
    monkeypatch.setenv("LLM_REASONING", "low")
    fake_llm.responder = lambda req: "ok"
    _client().chat("s", "u")
    req = fake_llm.chat_requests()[-1]
    assert req["top_k"] == 20 and req["reasoning_effort"] == "low"


def test_context_overflow(fake_llm, monkeypatch):
    import requests as rq

    class R:
        status_code = 400
        text = "the request exceeds the available context size"
    monkeypatch.setattr(rq, "post", lambda *a, **k: R())
    with pytest.raises(ContextOverflowError):
        _client().chat("s", "u")


def test_runtime_usage(fake_llm):
    fake_llm.responder = lambda req: "x" * 40
    rt = create_runtime(quiet=True)
    rt.llm.chat("s", "u" * 100)
    assert rt.usage.calls == 1 and rt.usage.completion_tokens == 10
    assert rt.tier == "standard"
