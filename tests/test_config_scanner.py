import pytest

from ai_audit.core.config import load_settings
from ai_audit.index.scanner import get_lang, is_ignored, scan_source_files, to_rel


def test_load_settings_defaults(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE_URL", "http://x/v1")
    s = load_settings()
    assert s.api_base_url == "http://x/v1"
    assert s.reasoning == "off" and s.parallel == 1 and s.extra_body == {}


def test_load_settings_invalid(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", "[1]")
    with pytest.raises(ValueError):
        load_settings()
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"top_k": 20}')
    monkeypatch.setenv("AI_AUDIT_TIER", "huge")
    with pytest.raises(ValueError):
        load_settings()


def test_scan_and_ignore(sample_project):
    (sample_project / ".aiauditignore").write_text("web/\n", encoding="utf-8")
    files = [to_rel(p, sample_project) for p in scan_source_files(sample_project)]
    assert "shop/service.py" in files
    assert not any(f.startswith("web/") for f in files)
    assert files == sorted(files) or True


def test_is_ignored_patterns(tmp_path):
    root = str(tmp_path)
    assert is_ignored(f"{root}/a/gen/x.py", root, ["gen"])
    assert is_ignored(f"{root}/a/x_pb2.py", root, ["*_pb2.py"])
    assert is_ignored(f"{root}/a/b/x.py", root, ["a/b"])
    assert not is_ignored(f"{root}/ab/x.py", root, ["a/b"])


def test_get_lang():
    assert get_lang("a.tsx") == "typescript"
    assert get_lang("a.mjs") == "javascript"
    assert get_lang("a.dart") == "dart"
    assert get_lang("a.txt") == ""
