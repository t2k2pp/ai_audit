"""
git 情報の取得（git が無い・管理外のディレクトリでは空を返す）
"""
import subprocess


def _git(root: str, *args: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def is_git_repo(root: str) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"


def head_info(root: str) -> dict:
    if not is_git_repo(root):
        return {}
    return {
        "commit": _git(root, "rev-parse", "--short", "HEAD").strip(),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "dirty": bool(_git(root, "status", "--porcelain", "--", ".").strip()),
    }


def commit_log(root: str, max_count: int = 500) -> list[dict]:
    """
    コミット履歴（新しい順）。各要素: hash, date, author, subject, body, files[(status, path)]
    root 配下のパスに関係するコミットのみ。
    """
    if not is_git_repo(root):
        return []
    sep, end = "\x1f", "\x1e"
    out = _git(root, "log", f"--max-count={max_count}", "--name-status",
               f"--format={end}%h{sep}%ad{sep}%an{sep}%s{sep}%b{sep}", "--date=short", "--", ".",
               timeout=120)
    commits = []
    for block in out.split(end):
        if not block.strip():
            continue
        head, _, files_part = block.rpartition(sep)
        fields = head.split(sep)
        if len(fields) < 5:
            continue
        files = []
        for line in files_part.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                files.append((parts[0][:1], parts[-1]))
        commits.append({"hash": fields[0], "date": fields[1], "author": fields[2],
                        "subject": fields[3], "body": fields[4].strip(), "files": files})
    return commits


def commit_diff_stat(root: str, commit: str) -> str:
    return _git(root, "show", "--stat", "--format=", commit).strip()
