import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_llm import FakeLLM  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# テスト対象の環境変数（実環境の .env が混ざらないようにすべてリセットする）
_ENV_KEYS = [
    "LLM_API_BASE_URL", "LLM_API_KEY", "LLM_MODEL_NAME", "LLM_MAX_OUTPUT_TOKENS",
    "LLM_CONTEXT_LENGTH", "LLM_CONTEXT_BUDGET", "AI_AUDIT_TIER", "LLM_REASONING",
    "LLM_EXTRA_BODY", "LLM_PARALLEL", "LLM_EMBEDDING_MODEL", "AI_AUDIT_DATA_DIR",
    "LLM_SERVER_TYPE",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_AUDIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AI_AUDIT_NO_DOTENV", "1")
    yield


@pytest.fixture
def fake_llm(monkeypatch):
    server = FakeLLM().start()
    monkeypatch.setenv("LLM_API_BASE_URL", server.base_url)
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_MODEL_NAME", "fake-model")
    yield server
    server.stop()


@pytest.fixture
def sample_project(tmp_path) -> Path:
    """サンプルプロジェクトを一時ディレクトリにコピーして返す（成果物で汚さないため）。"""
    dest = tmp_path / "sample_project"
    shutil.copytree(FIXTURES / "sample_project", dest)
    return dest
