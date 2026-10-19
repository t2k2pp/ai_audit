import pytest

from ai_audit.core.budget import TokenCounter, compute_budget, estimate_tokens
from ai_audit.core.config import Settings
from ai_audit.core.server_probe import ServerProfile


@pytest.mark.parametrize("ctx,tier,effective", [
    (4096, "compact", 4096),
    (8192, "compact", 8192),
    (32768, "standard", 16384),
    (131072, "standard", 65536),
    (262144, "large", 131072),
    (1_000_000, "large", 131072),
])
def test_compute_budget_tiers(ctx, tier, effective):
    b = compute_budget(Settings(), ServerProfile(context_length=ctx))
    assert (b.tier, b.effective) == (tier, effective)
    assert b.input_budget + b.output_reserve < b.effective


def test_compact_keeps_input_room():
    b = compute_budget(Settings(reasoning="high"), ServerProfile(context_length=4096))
    assert b.output_reserve <= 2048 and b.input_budget >= 1800


def test_overrides():
    s = Settings(context_budget=8000, tier="large", max_output_tokens=500)
    b = compute_budget(s, ServerProfile(context_length=262144))
    assert (b.effective, b.tier, b.output_reserve) == (8000, "large", 500)


def test_estimate_and_calibration(tmp_path):
    assert estimate_tokens("abcd" * 100) < estimate_tokens("あ" * 400)
    c = TokenCounter(Settings(model_name="m", data_dir=tmp_path))
    text = "x = 1\n" * 500
    before = c.count(text)
    for _ in range(10):
        c.observe(text, estimate_tokens(text) * 2)
    assert c.count(text) > before
    c.save()
    assert TokenCounter(Settings(model_name="m", data_dir=tmp_path)).factor == pytest.approx(c.factor)


def test_remote_tokenize(fake_llm):
    from ai_audit.core.config import load_settings
    from ai_audit.core.server_probe import probe_server
    s = load_settings()
    c = TokenCounter(s, probe_server(s, use_cache=False))
    assert c.count("a" * 8000) == 2000  # ダミーサーバは 4 文字 = 1 トークン
