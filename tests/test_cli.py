import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args, env, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *args], capture_output=True,
                          text=True, env=env, cwd=cwd or ROOT, timeout=120, encoding="utf-8")


def _env(fake_llm):
    env = dict(os.environ)
    env.update(LLM_API_BASE_URL=fake_llm.base_url, LLM_MODEL_NAME="fake-model", LLM_API_KEY="k")
    return env


def test_cli_design_doc_and_status(fake_llm, sample_project):
    from responders import smart
    fake_llm.responder = smart
    env = _env(fake_llm)
    r = _run(["generate_design_doc", str(sample_project), "--tier", "compact"], env)
    assert r.returncode == 0, r.stderr
    assert "ティア=compact" in r.stdout
    assert (sample_project / "_design" / "detail.md").exists()
    r = _run(["status", str(sample_project)], env)
    assert "[design_doc] 完了" in r.stdout


def test_cli_config_probe(fake_llm):
    r = _run(["config", "probe"], _env(fake_llm))
    assert r.returncode == 0, r.stderr
    assert "llamacpp" in r.stdout and "ティア=standard" in r.stdout


def test_cli_missing_settings(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    r = _run(["generate_design_doc", str(tmp_path)], env, cwd=tmp_path)
    assert r.returncode == 1 and "LLM_API_BASE_URL" in r.stderr
