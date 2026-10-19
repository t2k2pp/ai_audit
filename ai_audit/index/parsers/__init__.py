"""
パーサーの入口: 拡張子に応じて言語別パーサーを選ぶ
"""
import hashlib
import os

from ..model import ParsedFile
from ..scanner import get_lang, read_source, to_rel
from .dart import parse_dart
from .javascript import parse_javascript
from .python import parse_python


def parse_source(rel_path: str, source: str) -> ParsedFile:
    """ソース文字列を解析する（rel_path はシンボル ID に使うリポジトリ相対パス）。"""
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    lang = get_lang(rel_path)
    ext = os.path.splitext(rel_path)[1].lower()
    if lang == "python":
        return parse_python(rel_path, source, sha)
    if lang in ("javascript", "typescript"):
        return parse_javascript(rel_path, source, sha, lang, ext)
    if lang == "dart":
        return parse_dart(rel_path, source, sha)
    return ParsedFile(path=rel_path, lang="", sha256=sha, loc=source.count("\n") + 1,
                      error="未対応の言語です")


def parse_file(path: str, root: str) -> ParsedFile:
    return parse_source(to_rel(path, root), read_source(path))
