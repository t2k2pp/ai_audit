"""
ASTパーサー: Pythonソースコードを関数・クラス単位にチャンク化する
"""
import ast
import os
from typing import Generator


def parse_chunks(file_path: str) -> list[dict]:
    """
    Pythonファイルを解析し、関数・クラス単位のチャンクリストを返す。

    Args:
        file_path: 解析対象のPythonファイルパス
        
    Returns:
        チャンクの辞書リスト。各辞書は以下のキーを持つ:
          - chunk_id: str  ファイルパス:名前 形式の一意識別子
          - name: str      関数名またはクラス名
          - type: str      "function" または "class"
          - code: str      ソースコード文字列
          - lineno: int    開始行番号
    """
    abs_path = os.path.abspath(file_path)

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    try:
        tree = ast.parse(source, filename=abs_path)
    except SyntaxError as e:
        return []

    chunks = []
    source_lines = source.splitlines(keepends=True)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            code = _extract_source(source_lines, node)
            if code:
                chunks.append({
                    "chunk_id": f"{abs_path}:{node.name}",
                    "name": node.name,
                    "type": "function",
                    "code": code,
                    "lineno": node.lineno,
                })
        elif isinstance(node, ast.ClassDef):
            code = _extract_source(source_lines, node)
            if code:
                chunks.append({
                    "chunk_id": f"{abs_path}:{node.name}",
                    "name": node.name,
                    "type": "class",
                    "code": code,
                    "lineno": node.lineno,
                })

    # 行番号順にソート
    chunks.sort(key=lambda c: c["lineno"])
    return chunks


def _extract_source(source_lines: list[str], node: ast.AST) -> str:
    """ASTノードに対応するソースコード文字列を返す。"""
    try:
        start = node.lineno - 1  # 0-indexed
        end = node.end_lineno    # end_lineno は1-indexed（スライスに使えるのでそのまま）
        return "".join(source_lines[start:end])
    except (AttributeError, IndexError):
        return ""


def scan_python_files(directory: str) -> Generator[str, None, None]:
    """
    ディレクトリを再帰スキャンしてPythonファイルのパスを返す。

    Args:
        directory: スキャン対象ディレクトリ

    Yields:
        Pythonファイルの絶対パス
    """
    abs_dir = os.path.abspath(directory)
    for root, dirs, files in os.walk(abs_dir):
        # __pycache__ や .venv などを除外
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv", "venv", ".git", "node_modules")]
        for filename in files:
            if filename.endswith(".py"):
                yield os.path.join(root, filename)
