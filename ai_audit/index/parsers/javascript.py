"""
JavaScript / TypeScript パーサー（tree-sitter）

対象シンボル:
  - function / generator function 宣言、トップレベルの const f = () => {} / function 式
  - class（メソッド、関数値のフィールドを method として子に持つ）
  - TypeScript の interface / type alias / enum
"""
from ..model import Call, ImportRef, ParsedFile, Symbol, make_symbol_id

_FUNC_DECL = {"function_declaration", "generator_function_declaration"}
_CLASS_DECL = {"class_declaration", "abstract_class_declaration", "class"}
_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_TYPE_DECL = {"interface_declaration": "interface", "type_alias_declaration": "type",
              "enum_declaration": "enum"}
_BODY_PARENTS = _FUNC_DECL | _FUNC_VALUES | {"method_definition"}


def _get_parser(ext: str):
    """拡張子に応じた tree-sitter Parser を返す（未インストールなら None）。"""
    try:
        from tree_sitter import Language, Parser
        if ext in (".ts", ".tsx"):
            import tree_sitter_typescript as tsts
            lang = tsts.language_tsx() if ext == ".tsx" else tsts.language_typescript()
        else:
            import tree_sitter_javascript as tsjs
            lang = tsjs.language()
        return Parser(Language(lang))
    except ImportError:
        return None


class _Ctx:
    def __init__(self, path: str, lang: str, src: bytes):
        self.path, self.lang, self.src = path, lang, src

    def text(self, node) -> str:
        return self.src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _name(node, ctx: _Ctx) -> str:
    n = node.child_by_field_name("name")
    if n is not None:
        return ctx.text(n)
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "property_identifier"):
            return ctx.text(c)
    return ""


def _doc(node, ctx: _Ctx) -> str:
    """直前の /** */ コメントを返す。"""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        t = ctx.text(prev)
        if t.startswith("/**") and prev.end_point[0] >= node.start_point[0] - 1:
            lines = [ln.strip().lstrip("*").strip() for ln in t[3:-2].splitlines()]
            return "\n".join(ln for ln in lines if ln)
    return ""


def _signature(node, ctx: _Ctx) -> str:
    """本体ブロックの手前までをシグネチャとする（最大 300 文字）。"""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    sig = ctx.src[node.start_byte:end].decode("utf-8", errors="replace")
    sig = " ".join(sig.split())
    for tail in ("{", "=>"):
        if sig.endswith(tail):
            sig = sig[: -len(tail)].rstrip()
    return sig[:300]


def _calls(node, ctx: _Ctx) -> list[Call]:
    calls = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("call_expression", "new_expression"):
            fn = n.child_by_field_name("function") or n.child_by_field_name("constructor")
            if fn is not None and fn.type in ("identifier", "member_expression"):
                calls.append(Call(" ".join(ctx.text(fn).split()), n.start_point[0] + 1))
        stack.extend(n.children)
    calls.sort(key=lambda c: c.line)
    return calls


def _heritage(node, ctx: _Ctx) -> list[str]:
    bases = []
    for c in node.children:
        if c.type == "class_heritage":
            for clause in c.children:
                for t in clause.named_children:
                    bases.append(ctx.text(t))
                if clause.type == "identifier":  # JS: class A extends B
                    bases.append(ctx.text(clause))
    return bases


def _add(pf: ParsedFile, ctx: _Ctx, node, outer, name: str, kind: str, prefix: str,
         parent: str | None, exported: bool, body_for_calls=None) -> Symbol:
    qual = f"{prefix}.{name}" if prefix else name
    sym = Symbol(
        id=make_symbol_id(ctx.path, qual), file=ctx.path, name=name, qualname=qual, kind=kind,
        lang=ctx.lang, start_line=outer.start_point[0] + 1, end_line=outer.end_point[0] + 1,
        signature=_signature(node, ctx), doc=_doc(outer, ctx), parent=parent,
        calls=_calls(body_for_calls, ctx) if body_for_calls is not None else [],
        exported=exported,
    )
    pf.symbols.append(sym)
    return sym


def _visit_class(pf, ctx, node, outer, prefix, parent, exported):
    name = _name(node, ctx) or "default"
    cls = _add(pf, ctx, node, outer, name, "class", prefix, parent, exported)
    cls.bases = _heritage(node, ctx)
    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.named_children:
        if member.type == "method_definition":
            _add(pf, ctx, member, member, _name(member, ctx), "method", cls.qualname, cls.id,
                 exported, member)
        elif member.type in ("public_field_definition", "field_definition"):
            value = member.child_by_field_name("value")
            if value is not None and value.type in _FUNC_VALUES:
                prop = member.child_by_field_name("name") or member.child_by_field_name("property")
                mname = ctx.text(prop) if prop is not None else _name(member, ctx)
                sym = _add(pf, ctx, value, member, mname, "method", cls.qualname, cls.id, exported, value)
                sym.signature = f"{mname} = {sym.signature}"[:300]


def _visit_declaration(pf, ctx, node, outer, exported):
    """lexical_declaration / variable_declaration の関数値を拾う。"""
    for decl in node.named_children:
        if decl.type != "variable_declarator":
            continue
        name_node, value = decl.child_by_field_name("name"), decl.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        if value.type in _FUNC_VALUES:
            sym = _add(pf, ctx, value, outer, ctx.text(name_node), "function", "", None, exported, value)
            sym.signature = f"{ctx.text(name_node)} = {sym.signature}"[:300]
        elif value.type == "class":
            _visit_class(pf, ctx, value, outer, "", None, exported)
        elif value.type == "call_expression":
            _maybe_require(pf, ctx, value)


def _maybe_require(pf, ctx, call):
    fn = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if fn is not None and ctx.text(fn) == "require" and args is not None and args.named_children:
        arg = args.named_children[0]
        if arg.type == "string":
            pf.imports.append(ImportRef(ctx.text(arg).strip("'\"`"), [], call.start_point[0] + 1))


def _visit_top(pf, ctx, node, outer=None, exported=False):
    outer = outer or node
    t = node.type
    if t in _FUNC_DECL:
        _add(pf, ctx, node, outer, _name(node, ctx), "function", "", None, exported, node)
    elif t in _CLASS_DECL:
        _visit_class(pf, ctx, node, outer, "", None, exported)
    elif t in _TYPE_DECL:
        _add(pf, ctx, node, outer, _name(node, ctx), _TYPE_DECL[t], "", None, exported)
    elif t in ("lexical_declaration", "variable_declaration"):
        _visit_declaration(pf, ctx, node, outer, exported)
    elif t == "export_statement":
        decl = node.child_by_field_name("declaration")
        targets = [decl] if decl is not None else node.named_children
        for child in targets:
            if child.type in _FUNC_DECL | _CLASS_DECL | set(_TYPE_DECL) | {
                    "lexical_declaration", "variable_declaration"}:
                _visit_top(pf, ctx, child, node, True)
        src = node.child_by_field_name("source")
        if src is not None:  # export { x } from "./y"
            pf.imports.append(ImportRef(ctx.text(src).strip("'\"`"), [], node.start_point[0] + 1))
    elif t == "import_statement":
        src = node.child_by_field_name("source")
        if src is None:
            src = next((c for c in node.children if c.type == "string"), None)
        if src is not None:
            names = []
            for c in node.children:
                if c.type == "import_clause":
                    names = [ctx.text(i) for i in _walk(c) if i.type == "identifier"]
            pf.imports.append(ImportRef(ctx.text(src).strip("'\"`"), names, node.start_point[0] + 1))
    elif t == "expression_statement":
        for c in _walk(node):
            if c.type == "call_expression":
                _maybe_require(pf, ctx, c)


def _walk(node):
    """前順（ソース順）で全ノードを返す。"""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def make_skeleton(root, src: bytes) -> str:
    """関数・メソッド本体を { /* ... */ } に置き換えたスケルトンを返す。"""
    cuts = []  # (start_byte, end_byte)
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in _BODY_PARENTS:
            body = n.child_by_field_name("body")
            if body is not None and body.type == "statement_block":
                if body.end_point[0] > body.start_point[0]:
                    cuts.append((body.start_byte, body.end_byte))
                continue
        stack.extend(n.children)
    out, pos = [], 0
    for start, end in sorted(cuts):
        out.append(src[pos:start])
        out.append(b"{ /* ... */ }")
        pos = end
    out.append(src[pos:])
    text = b"".join(out).decode("utf-8", errors="replace")
    return "\n".join(line for line in text.splitlines() if line.strip())


def parse_javascript(path: str, source: str, sha: str, lang: str, ext: str) -> ParsedFile:
    pf = ParsedFile(path=path, lang=lang, sha256=sha, loc=source.count("\n") + 1)
    parser = _get_parser(ext)
    if parser is None:
        pf.error = "tree-sitter が未インストールのため解析できません"
        return pf
    src = source.encode("utf-8")
    tree = parser.parse(src)
    ctx = _Ctx(path, lang, src)
    root = tree.root_node
    if root.named_children and root.named_children[0].type == "comment":
        first = ctx.text(root.named_children[0])
        if first.startswith("/**"):
            pf.module_doc = first.strip("/*").strip()
    for node in root.named_children:
        _visit_top(pf, ctx, node)
    if root.has_error:
        pf.error = "構文エラーを含みます（解析できた範囲のみ記録）"
    pf.skeleton = make_skeleton(root, src)
    return pf
