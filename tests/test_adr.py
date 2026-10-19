import subprocess

from ai_audit.usecases.design_doc import generate_design_doc
from ai_audit.usecases.design_doc.adr import score_commit, select_commits
from responders import smart


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})


def test_score_and_select():
    big = {"hash": "a", "subject": "refactor: split service", "body": "",
           "files": [("A", f"x{i}.py") for i in range(6)] + [("M", "requirements.txt")]}
    small = {"hash": "b", "subject": "typo", "body": "", "files": [("M", "README.md")]}
    assert score_commit(big) > score_commit(small)
    assert select_commits([small, big])[0]["hash"] == "a"


def test_adr_generated_for_large_git_repo(fake_llm, sample_project, monkeypatch):
    _git(sample_project, "init", "-q")
    _git(sample_project, "add", "-A")
    _git(sample_project, "commit", "-q", "-m", "feat: 初期構成を導入")
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "262144")
    fake_llm.responder = smart
    out = generate_design_doc(str(sample_project), quiet=True)
    assert out["ok"]
    adr_dir = sample_project / "_design" / "adr"
    assert (adr_dir / "README.md").exists() and (adr_dir / "0001.md").exists()
    text = (adr_dir / "0001.md").read_text(encoding="utf-8")
    assert "初期構成" in text and "abc1234" not in text  # 履歴にないハッシュは除去
    assert "large" in (sample_project / "_design" / "README.md").read_text(encoding="utf-8")
