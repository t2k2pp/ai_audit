"""
ASTパーサー: Python / JavaScript / TypeScript / Dart ソースコードを関数・クラス単位にチャンク化する
"""
import ast
import fnmatch
import os
from typing import Generator

# JS/TS 拡張子セット
_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
# Dart 拡張子セット
_DART_EXTENSIONS = {".dart"}
# 除外ディレクトリ
_EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", ".git", "node_modules", "dist", "build", ".next"}

# ---------------------------------------------------------------------------
# .aiauditignore サポート
# ---------------------------------------------------------------------------

def load_aiauditignore(directory: str) -> list[str]:
    """
    指定ディレクトリの .aiauditignore ファイルを読み込み、パターンリストを返す。
    ファイルが存在しない場合は空リストを返す。

    .aiauditignore の書式（.gitignore と同様）:
      - # で始まる行はコメント
      - 空行は無視
      - * はファイル名内の任意の文字列にマッチ
      - ** はディレクトリ区切りを含む任意のパスにマッチ
      - 末尾の / はディレクトリのみをマッチ（現実装では / を除いたパターンとして扱う）
    """
    ignore_path = os.path.join(os.path.abspath(directory), ".aiauditignore")
    if not os.path.exists(ignore_path):
        return []
    patterns = []
    try:
        with open(ignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n\r")
                if line.startswith("#") or not line.strip():
                    continue
                # 末尾スラッシュはディレクトリ指定なので除去してパターンとして使用
                patterns.append(line.rstrip("/"))
    except OSError:
        pass
    return patterns


def is_ignored(file_path: str, root_dir: str, patterns: list[str]) -> bool:
    """
    ファイルパスが .aiauditignore のパターンにマッチするかどうかを返す。

    Args:
        file_path: チェック対象ファイルの絶対パス
        root_dir:  .aiauditignore が存在するルートディレクトリの絶対パス
        patterns:  load_aiauditignore() が返したパターンリスト

    Returns:
        True ならば除外対象
    """
    if not patterns:
        return False
    # ルートからの相対パス（区切り文字を / に統一）
    rel = os.path.relpath(file_path, root_dir).replace(os.sep, "/")
    for pattern in patterns:
        # パターン自体に / が含まれない場合: ファイル名のみでマッチ
        if "/" not in pattern:
            if fnmatch.fnmatch(os.path.basename(file_path), pattern):
                return True
        # パターンに / がある場合: 相対パス全体でマッチ（** も考慮）
        if fnmatch.fnmatch(rel, pattern):
            return True
        # パターンがディレクトリ prefix の場合（例: build_tmp → build_tmp/以下全て）
        if rel.startswith(pattern + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# 公開API（拡張子で自動振り分け）
# ---------------------------------------------------------------------------

def parse_chunks(file_path: str) -> list[dict]:
    """
    ソースファイルを解析し、関数・クラス単位のチャンクリストを返す。
    拡張子に応じて Python / JS/TS / Dart パーサーを自動選択する。

    Args:
        file_path: 解析対象ファイルパス（.py / .js / .ts / .jsx / .tsx / .dart）

    Returns:
        チャンクの辞書リスト。各辞書は以下のキーを持つ:
          - chunk_id: str  ファイルパス:名前 形式の一意識別子
          - name: str      関数名またはクラス名
          - type: str      "function" または "class"
          - code: str      ソースコード文字列
          - lineno: int    開始行番号
          - lang: str      "python" / "javascript" / "typescript" / "dart"
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _JS_EXTENSIONS:
        return parse_chunks_js(file_path)
    if ext in _DART_EXTENSIONS:
        return parse_chunks_dart(file_path)
    return _parse_chunks_python(file_path)


def generate_skeleton(file_path: str) -> str:
    """
    ソースファイルのスケルトンコードを生成する。
    拡張子に応じて Python / JS/TS / Dart スケルトナーを自動選択する。

    Args:
        file_path: 対象ファイルパス

    Returns:
        スケルトンコード文字列（実装ブロック除去済み）
        パースエラーの場合は空文字列
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _JS_EXTENSIONS:
        return generate_skeleton_js(file_path)
    if ext in _DART_EXTENSIONS:
        return generate_skeleton_dart(file_path)
    return _generate_skeleton_python(file_path)


def scan_python_files(directory: str) -> Generator[str, None, None]:
    """
    ディレクトリを再帰スキャンしてPythonファイルのパスを返す（後方互換維持）。
    .aiauditignore が存在する場合はそのパターンに従って除外する。

    Args:
        directory: スキャン対象ディレクトリ

    Yields:
        Pythonファイルの絶対パス
    """
    abs_dir = os.path.abspath(directory)
    patterns = load_aiauditignore(abs_dir)
    for root, dirs, files in os.walk(abs_dir):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for filename in files:
            if filename.endswith(".py"):
                full_path = os.path.join(root, filename)
                if not is_ignored(full_path, abs_dir, patterns):
                    yield full_path


def scan_js_files(directory: str) -> Generator[str, None, None]:
    """
    ディレクトリを再帰スキャンしてJS/TSファイルのパスを返す。
    .aiauditignore が存在する場合はそのパターンに従って除外する。

    Args:
        directory: スキャン対象ディレクトリ

    Yields:
        JS/TSファイルの絶対パス（.js / .jsx / .ts / .tsx）
    """
    abs_dir = os.path.abspath(directory)
    patterns = load_aiauditignore(abs_dir)
    for root, dirs, files in os.walk(abs_dir):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for filename in files:
            if os.path.splitext(filename)[1].lower() in _JS_EXTENSIONS:
                full_path = os.path.join(root, filename)
                if not is_ignored(full_path, abs_dir, patterns):
                    yield full_path


def scan_source_files(directory: str) -> Generator[str, None, None]:
    """
    ディレクトリを再帰スキャンしてPython・JS/TS・Dartファイルのパスを返す。
    .aiauditignore が存在する場合はそのパターンに従って除外する。

    Args:
        directory: スキャン対象ディレクトリ

    Yields:
        ソースファイルの絶対パス（.py / .js / .jsx / .ts / .tsx / .dart）
    """
    abs_dir = os.path.abspath(directory)
    patterns = load_aiauditignore(abs_dir)
    for root, dirs, files in os.walk(abs_dir):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext == ".py" or ext in _JS_EXTENSIONS or ext in _DART_EXTENSIONS:
                full_path = os.path.join(root, filename)
                if not is_ignored(full_path, abs_dir, patterns):
                    yield full_path


def get_lang(file_path: str) -> str:
    """ファイルパスから言語識別子を返す。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".ts", ".tsx"):
        return "typescript"
    if ext in (".js", ".jsx"):
        return "javascript"
    if ext == ".dart":
        return "dart"
    return "python"


# ---------------------------------------------------------------------------
# Python パーサー（既存実装）
# ---------------------------------------------------------------------------

def _parse_chunks_python(file_path: str) -> list[dict]:
    """Pythonファイルを解析し、関数・クラス単位のチャンクリストを返す。"""
    abs_path = os.path.abspath(file_path)

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    try:
        tree = ast.parse(source, filename=abs_path)
    except SyntaxError:
        return []

    chunks = []
    source_lines = source.splitlines(keepends=True)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            code = _extract_source_python(source_lines, node)
            if code:
                chunks.append({
                    "chunk_id": f"{abs_path}:{node.name}",
                    "name": node.name,
                    "type": "function",
                    "code": code,
                    "lineno": node.lineno,
                    "lang": "python",
                })
        elif isinstance(node, ast.ClassDef):
            code = _extract_source_python(source_lines, node)
            if code:
                chunks.append({
                    "chunk_id": f"{abs_path}:{node.name}",
                    "name": node.name,
                    "type": "class",
                    "code": code,
                    "lineno": node.lineno,
                    "lang": "python",
                })

    chunks.sort(key=lambda c: c["lineno"])
    return chunks


def _extract_source_python(source_lines: list[str], node: ast.AST) -> str:
    """ASTノードに対応するソースコード文字列を返す。"""
    try:
        start = node.lineno - 1  # 0-indexed
        end = node.end_lineno    # end_lineno は1-indexed（スライスに使えるのでそのまま）
        return "".join(source_lines[start:end])
    except (AttributeError, IndexError):
        return ""


class _SkeletonTransformer(ast.NodeTransformer):
    """
    ASTトランスフォーマー: 実装ブロック（関数・メソッドの本体）を削除し、
    クラス名・関数シグネチャ・Docstringのみを残したスケルトンに変換する。
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        return self._skeleton_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        return self._skeleton_function(node)

    def _skeleton_function(self, node):
        """関数の本体をPassに置き換え、Docstringのみ残す。"""
        new_body = []
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            new_body.append(node.body[0])
        new_body.append(ast.Pass())
        node.body = new_body
        return node


def _generate_skeleton_python(file_path: str) -> str:
    """
    指定PythonファイルのスケルトンコードをPython ASTで生成する。

    Args:
        file_path: 対象Pythonファイルパス

    Returns:
        スケルトンコード文字列（実装ブロック除去済み）
        パースエラーの場合は空文字列
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
    except SyntaxError:
        return ""

    transformer = _SkeletonTransformer()
    skeleton_tree = transformer.visit(tree)
    ast.fix_missing_locations(skeleton_tree)

    try:
        return ast.unparse(skeleton_tree)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# JS/TS パーサー（tree-sitter）
# ---------------------------------------------------------------------------

def _get_js_parser(file_path: str):
    """
    ファイルの拡張子に応じた tree-sitter Parser を返す。
    tree-sitter が未インストールの場合は None を返す。
    """
    try:
        import tree_sitter_javascript as tsjs
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser as TSParser
    except ImportError:
        return None, None

    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".ts",):
        lang = Language(tsts.language_typescript())
    elif ext in (".tsx",):
        lang = Language(tsts.language_tsx())
    else:
        # .js / .jsx
        lang = Language(tsjs.language())

    return TSParser(lang), lang


def parse_chunks_js(file_path: str) -> list[dict]:
    """
    JS/TSファイルを解析し、関数・クラス・アロー関数・メソッド単位のチャンクリストを返す。

    Args:
        file_path: 解析対象のJS/TSファイルパス

    Returns:
        チャンクの辞書リスト（parse_chunks と同形式）
    """
    abs_path = os.path.abspath(file_path)
    parser, _ = _get_js_parser(abs_path)
    if parser is None:
        return []

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines(keepends=True)
    lang = get_lang(abs_path)

    chunks = []
    _collect_js_chunks(tree.root_node, source_bytes, source_lines, abs_path, lang, chunks)
    chunks.sort(key=lambda c: c["lineno"])
    return chunks


def _collect_js_chunks(
    node,
    source_bytes: bytes,
    source_lines: list[str],
    abs_path: str,
    lang: str,
    chunks: list[dict],
) -> None:
    """
    tree-sitter ASTノードを再帰的に走査してチャンクを収集する。
    トップレベルの関数・クラス・export された要素を対象とする。
    """
    # 関数宣言（function foo() {}）
    if node.type == "function_declaration":
        name = _get_js_node_name(node, source_bytes)
        if name:
            code = _node_source(node, source_lines)
            chunks.append(_make_js_chunk(abs_path, name, "function", code, node.start_point.row + 1, lang))
        return  # 内部のネスト関数は収集しない

    # クラス宣言（class Foo {}）
    if node.type == "class_declaration":
        name = _get_js_node_name(node, source_bytes)
        if name:
            code = _node_source(node, source_lines)
            chunks.append(_make_js_chunk(abs_path, name, "class", code, node.start_point.row + 1, lang))
        return

    # export 文（export function / export class / export const foo = () => {}）
    if node.type == "export_statement":
        _collect_js_export(node, source_bytes, source_lines, abs_path, lang, chunks)
        return

    # lexical_declaration のトップレベルアロー関数（const foo = () => {}）
    if node.type == "lexical_declaration":
        _collect_js_arrow(node, source_bytes, source_lines, abs_path, lang, chunks)
        return

    # 再帰で子ノードを処理（プログラムルートのみ）
    if node.type == "program":
        for child in node.children:
            _collect_js_chunks(child, source_bytes, source_lines, abs_path, lang, chunks)


def _collect_js_export(node, source_bytes, source_lines, abs_path, lang, chunks):
    """export 文からチャンクを収集する。"""
    for child in node.children:
        if child.type == "function_declaration":
            name = _get_js_node_name(child, source_bytes)
            if name:
                code = _node_source(node, source_lines)
                chunks.append(_make_js_chunk(abs_path, name, "function", code, node.start_point.row + 1, lang))
        elif child.type == "class_declaration":
            name = _get_js_node_name(child, source_bytes)
            if name:
                code = _node_source(node, source_lines)
                chunks.append(_make_js_chunk(abs_path, name, "class", code, node.start_point.row + 1, lang))
        elif child.type == "lexical_declaration":
            _collect_js_arrow(child, source_bytes, source_lines, abs_path, lang, chunks,
                               parent_node=node)


def _collect_js_arrow(node, source_bytes, source_lines, abs_path, lang, chunks, parent_node=None):
    """lexical_declaration からアロー関数チャンクを収集する。"""
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node and value_node and value_node.type == "arrow_function":
                name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
                src_node = parent_node if parent_node else node
                code = _node_source(src_node, source_lines)
                chunks.append(_make_js_chunk(abs_path, name, "function", code, src_node.start_point.row + 1, lang))


def _get_js_node_name(node, source_bytes: bytes) -> str:
    """
    関数・クラスノードの名前を返す。
    JS は identifier、TS は type_identifier を使うため両方を探す。
    """
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
    return ""


def _get_child_text(node, child_type: str, source_bytes: bytes) -> str:
    """指定タイプの子ノードのテキストを返す。"""
    for child in node.children:
        if child.type == child_type:
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
    return ""


def _node_source(node, source_lines: list[str]) -> str:
    """ASTノードに対応するソースコード文字列を返す。"""
    start = node.start_point.row
    end = node.end_point.row + 1
    return "".join(source_lines[start:end])


def _make_js_chunk(abs_path, name, chunk_type, code, lineno, lang):
    return {
        "chunk_id": f"{abs_path}:{name}",
        "name": name,
        "type": chunk_type,
        "code": code,
        "lineno": lineno,
        "lang": lang,
    }


def generate_skeleton_js(file_path: str) -> str:
    """
    JS/TSファイルのスケルトンコードを生成する。
    関数・メソッドの本体を除去し、シグネチャ・JSDoc・型アノテーションのみを残す。

    Args:
        file_path: 対象JS/TSファイルパス

    Returns:
        スケルトンコード文字列
        パースエラーまたは tree-sitter 未インストールの場合は空文字列
    """
    abs_path = os.path.abspath(file_path)
    parser, _ = _get_js_parser(abs_path)
    if parser is None:
        return ""

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines(keepends=True)

    skeleton_lines = list(source_lines)  # コピー
    _strip_js_function_bodies(tree.root_node, source_bytes, skeleton_lines)

    return "".join(skeleton_lines)


def _strip_js_function_bodies(node, source_bytes: bytes, skeleton_lines: list[str]) -> None:
    """
    tree-sitter ASTを再帰走査し、関数・メソッドの本体ブロックを「{ /* ... */ }」に置き換える。
    """
    # 本体を持つノードタイプ
    BODY_PARENT_TYPES = {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "generator_function_declaration",
        "generator_function",
    }

    if node.type in BODY_PARENT_TYPES:
        # 本体（statement_block）を探して置換
        for child in node.children:
            if child.type == "statement_block":
                _replace_block_with_stub(child, skeleton_lines)
                return  # 本体を置換したら内部は処理しない

    for child in node.children:
        _strip_js_function_bodies(child, source_bytes, skeleton_lines)


def _replace_block_with_stub(block_node, skeleton_lines: list[str]) -> None:
    """
    statement_block ノードの内容を「{ /* ... */ }」に置き換える。
    開始行に「{」を残し、閉じ括弧行に「}」を残し、中間行を空にする。
    """
    start_row = block_node.start_point.row
    end_row = block_node.end_point.row

    if start_row == end_row:
        # 1行ブロック: そのまま
        return

    # 開始行: 「{」より後を「 /* ... */」に
    start_line = skeleton_lines[start_row]
    brace_pos = start_line.rfind("{")
    if brace_pos >= 0:
        skeleton_lines[start_row] = start_line[:brace_pos + 1] + " /* ... */\n"

    # 中間行を空に
    for row in range(start_row + 1, end_row):
        skeleton_lines[row] = ""

    # 閉じ行: インデントのみ残す
    end_line = skeleton_lines[end_row]
    stripped = end_line.lstrip()
    indent = end_line[: len(end_line) - len(stripped)]
    skeleton_lines[end_row] = indent + "}\n"


# ---------------------------------------------------------------------------
# Dart パーサー（tree-sitter-language-pack）
# ---------------------------------------------------------------------------

def _get_dart_parser():
    """
    tree-sitter-language-pack から Dart パーサーを返す。
    未インストールの場合は None を返す。
    """
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser("dart")
    except (ImportError, Exception):
        return None


def parse_chunks_dart(file_path: str) -> list[dict]:
    """
    Dartファイルを解析し、クラス・トップレベル関数単位のチャンクリストを返す。

    Args:
        file_path: 解析対象の Dart ファイルパス

    Returns:
        チャンクの辞書リスト（parse_chunks と同形式）
    """
    abs_path = os.path.abspath(file_path)
    parser = _get_dart_parser()
    if parser is None:
        return []

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines(keepends=True)

    chunks = []
    _collect_dart_chunks(tree.root_node, source_bytes, source_lines, abs_path, chunks)
    chunks.sort(key=lambda c: c["lineno"])
    return chunks


def _collect_dart_chunks(node, source_bytes, source_lines, abs_path, chunks):
    """
    Dart ASTのルートノードを走査してチャンクを収集する。
    - class_definition: クラス全体
    - function_signature + function_body ペア: トップレベル関数
    """
    if node.type != "program":
        return

    children = list(node.children)
    i = 0
    while i < len(children):
        child = children[i]

        # クラス定義
        if child.type == "class_definition":
            name = _get_dart_identifier(child, source_bytes)
            if name:
                code = _node_source(child, source_lines)
                chunks.append(_make_js_chunk(abs_path, name, "class", code, child.start_point.row + 1, "dart"))
            i += 1

        # トップレベル関数: function_signature の直後に function_body が来る
        elif child.type == "function_signature":
            name = _get_dart_function_name(child, source_bytes)
            if name and i + 1 < len(children) and children[i + 1].type == "function_body":
                func_node = children[i + 1]
                # function_signature から function_body までをまとめてコードとして取得
                start_row = child.start_point.row
                end_row = func_node.end_point.row + 1
                code = "".join(source_lines[start_row:end_row])
                chunks.append(_make_js_chunk(abs_path, name, "function", code, start_row + 1, "dart"))
                i += 2  # function_signature と function_body を両方消費
            else:
                i += 1

        else:
            i += 1


def _get_dart_identifier(node, source_bytes: bytes) -> str:
    """class_definition ノードから識別子（クラス名）を返す。"""
    for child in node.children:
        if child.type == "identifier":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
    return ""


def _get_dart_function_name(node, source_bytes: bytes) -> str:
    """
    function_signature ノードから関数名を返す。
    function_signature の子に identifier が含まれる（戻り値型の後）。
    """
    for child in node.children:
        if child.type == "identifier":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
    return ""


def generate_skeleton_dart(file_path: str) -> str:
    """
    Dartファイルのスケルトンコードを生成する。
    function_body の block を「{ /* ... */ }」に置き換える。

    Args:
        file_path: 対象 Dart ファイルパス

    Returns:
        スケルトンコード文字列
        パースエラーまたは未インストールの場合は空文字列
    """
    abs_path = os.path.abspath(file_path)
    parser = _get_dart_parser()
    if parser is None:
        return ""

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            source = f.read()

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines(keepends=True)

    skeleton_lines = list(source_lines)
    _strip_dart_function_bodies(tree.root_node, source_bytes, skeleton_lines)

    return "".join(skeleton_lines)


def _strip_dart_function_bodies(node, source_bytes: bytes, skeleton_lines: list[str]) -> None:
    """
    Dart ASTを再帰走査し、function_body 内の block を「{ /* ... */ }」に置き換える。
    """
    if node.type == "function_body":
        for child in node.children:
            if child.type == "block":
                _replace_block_with_stub(child, skeleton_lines)
                return  # 内部は処理しない

    for child in node.children:
        _strip_dart_function_bodies(child, source_bytes, skeleton_lines)
