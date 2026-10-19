"""
ファイル走査: 対象ソースファイルの列挙と .aiauditignore による除外

.aiauditignore の書式（.gitignore の簡易版）:
  - # で始まる行はコメント、空行は無視
  - / を含まないパターンはファイル名・ディレクトリ名に対してマッチ
  - / を含むパターンはルートからの相対パスに対してマッチ（* は / も含めて任意の文字列）
  - 末尾の / はディレクトリ指定（そのディレクトリ以下をすべて除外）
"""
import fnmatch
import os
from typing import Iterator

PY_EXTENSIONS = {".py"}
JS_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTENSIONS = {".ts", ".tsx"}
DART_EXTENSIONS = {".dart"}
SOURCE_EXTENSIONS = PY_EXTENSIONS | JS_EXTENSIONS | TS_EXTENSIONS | DART_EXTENSIONS

EXCLUDE_DIRS = {
    "__pycache__", ".venv", "venv", ".git", "node_modules", "dist", "build", ".next",
    ".ai_audit", "_design", ".dart_tool", ".mypy_cache", ".pytest_cache",
}


def get_lang(file_path: str) -> str:
    """拡張子から言語識別子を返す（python / javascript / typescript / dart / 空文字）。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in PY_EXTENSIONS:
        return "python"
    if ext in TS_EXTENSIONS:
        return "typescript"
    if ext in JS_EXTENSIONS:
        return "javascript"
    if ext in DART_EXTENSIONS:
        return "dart"
    return ""


def is_source_file(file_path: str) -> bool:
    return os.path.splitext(file_path)[1].lower() in SOURCE_EXTENSIONS


def load_aiauditignore(directory: str) -> list[str]:
    """ディレクトリ直下の .aiauditignore を読み込み、パターンリストを返す。"""
    ignore_path = os.path.join(os.path.abspath(directory), ".aiauditignore")
    if not os.path.exists(ignore_path):
        return []
    patterns = []
    try:
        with open(ignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line.rstrip("/"))
    except OSError:
        pass
    return patterns


def is_ignored(file_path: str, root_dir: str, patterns: list[str]) -> bool:
    """ファイルパスが .aiauditignore のパターンにマッチするかを返す。"""
    if not patterns:
        return False
    rel = os.path.relpath(file_path, root_dir).replace(os.sep, "/")
    parts = rel.split("/")
    for pattern in patterns:
        if "/" not in pattern:
            # ファイル名・途中のディレクトリ名のいずれかにマッチすれば除外
            if any(fnmatch.fnmatch(p, pattern) for p in parts):
                return True
            continue
        pattern = pattern.lstrip("/")
        if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern + "/"):
            return True
    return False


def scan_source_files(directory: str) -> Iterator[str]:
    """
    ディレクトリを再帰走査して対象ソースファイルの絶対パスを返す（パス順で安定）。
    """
    abs_dir = os.path.abspath(directory)
    patterns = load_aiauditignore(abs_dir)
    for root, dirs, files in os.walk(abs_dir):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for filename in sorted(files):
            if not is_source_file(filename):
                continue
            full_path = os.path.join(root, filename)
            if not is_ignored(full_path, abs_dir, patterns):
                yield full_path


def to_rel(path: str, root: str) -> str:
    """ルートからの相対パス（区切りは /）を返す。"""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


def read_source(path: str) -> str:
    """ソースファイルを読み込む（UTF-8、失敗時は latin-1）。改行は \\n に統一する。"""
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1", newline="") as f:
            text = f.read()
    return text.replace("\r\n", "\n").replace("\r", "\n")
