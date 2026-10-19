import requests


def test_fake_llm_chat(fake_llm):
    fake_llm.responder = lambda req: "hello"
    r = requests.post(f"{fake_llm.base_url}/chat/completions", json={"messages": []}, timeout=5)
    assert r.json()["choices"][0]["message"]["content"] == "hello"
    assert len(fake_llm.chat_requests()) == 1
