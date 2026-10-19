"""
Python パーサー（標準 ast）
"""
import ast

from ..model import Call, ImportRef, ParsedFile, Symbol, make_symbol_id


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _collect_calls(func: ast.AST) -> list[Call]:
    """関数本体の呼び出しを集める（入れ子のクラス・関数定義の中も含める）。"""
    calls = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                calls.append(Call(name, node.lineno))
    calls.sort(key=lambda c: c.line)
    return calls


def _signature(node) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"


def _start_line(node) -> int:
    # デコレータがあればデコレータ行から
    lines = [d.lineno for d in getattr(node, "decorator_list", [])]
    return min([node.lineno] + lines)


class _SkeletonTransformer(ast.NodeTransformer):
    """関数本体を Docstring＋pass に置き換える。"""

    def _strip(self, node):
        body = []
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            body.append(node.body[0])
        body.append(ast.Pass())
        node.body = body
        return node

    def visit_FunctionDef(self, node):
        return self._strip(node)

    def visit_AsyncFunctionDef(self, node):
        return self._strip(node)


def make_skeleton(tree: ast.Module) -> str:
    import copy
    skeleton = _SkeletonTransformer().visit(copy.deepcopy(tree))
    ast.fix_missing_locations(skeleton)
    try:
        return ast.unparse(skeleton)
    except Exception:  # unparse は稀に失敗する（極端に深い式など）
        return ""


def parse_python(path: str, source: str, sha: str) -> ParsedFile:
    pf = ParsedFile(path=path, lang="python", sha256=sha, loc=source.count("\n") + 1)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        pf.error = f"SyntaxError: {e.msg} (line {e.lineno})"
        return pf
    pf.module_doc = ast.get_docstring(tree) or ""

    def visit(body, prefix: str, parent_id: str | None, in_class: bool):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{node.name}" if prefix else node.name
                is_class = isinstance(node, ast.ClassDef)
                kind = "class" if is_class else ("method" if in_class else "function")
                sym = Symbol(
                    id=make_symbol_id(path, qual), file=path, name=node.name, qualname=qual,
                    kind=kind, lang="python", start_line=_start_line(node),
                    end_line=node.end_lineno or node.lineno, signature=_signature(node),
                    doc=ast.get_docstring(node) or "", parent=parent_id,
                    bases=[ast.unparse(b) for b in node.bases] if is_class else [],
                    calls=[] if is_class else _collect_calls(node),
                    exported=not node.name.startswith("_"),
                )
                pf.symbols.append(sym)
                if is_class:
                    visit(node.body, qual, sym.id, True)
            elif isinstance(node, (ast.If, ast.Try)):
                # if TYPE_CHECKING: / try: import ... の中の定義も拾う
                for block in (getattr(node, "body", []), getattr(node, "orelse", []),
                              getattr(node, "finalbody", [])):
                    visit(block, prefix, parent_id, in_class)

    visit(tree.body, "", None, False)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                pf.imports.append(ImportRef(alias.name, [], node.lineno, 0))
        elif isinstance(node, ast.ImportFrom):
            pf.imports.append(ImportRef(node.module or "", [a.name for a in node.names],
                                        node.lineno, node.level))
    pf.skeleton = make_skeleton(tree)
    return pf
