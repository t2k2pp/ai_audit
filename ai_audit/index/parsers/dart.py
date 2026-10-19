"""
Dart パーサー（tree-sitter-language-pack）

対象シンボル: class / mixin / enum / extension、そのメソッド、トップレベル関数
"""
from ..model import Call, ImportRef, ParsedFile, Symbol, make_symbol_id

_CLASS_TYPES = {"class_definition": "class", "mixin_declaration": "class",
                "enum_declaration": "enum", "extension_declaration": "class"}
_SIG_TYPES = {"function_signature", "method_signature", "getter_signature", "setter_signature",
              "constructor_signature", "factory_constructor_signature"}


def _get_parser():
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser("dart")
    except Exception:  # 未インストール・言語未収録
        return None


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first(node, types):
    for c in node.children:
        if c.type in types:
            return c
    return None


def _sig_name(sig, src) -> str:
    inner = _first(sig, _SIG_TYPES) or sig
    ident = _first(inner, {"identifier"})
    return _text(ident, src) if ident is not None else ""


def _doc(node, src) -> str:
    lines = []
    prev = node.prev_sibling
    while prev is not None and prev.type in ("documentation_comment", "comment"):
        t = _text(prev, src)
        if not t.startswith("///"):
            break
        lines.insert(0, t[3:].strip())
        prev = prev.prev_sibling
    return "\n".join(lines)


def _calls(body, src) -> list[Call]:
    calls = []
    stack = [body]
    while stack:
        n = stack.pop()
        children = n.children
        for i, c in enumerate(children):
            if c.type == "selector" and _text(c, src).startswith("(") and i > 0:
                prev = children[i - 1]
                name = _text(prev, src).lstrip("?.").strip()
                if prev.type in ("identifier", "selector") and name and len(name) < 80:
                    calls.append(Call(name, c.start_point[0] + 1))
        stack.extend(children)
    calls.sort(key=lambda c: c.line)
    return calls


def _pairs(children):
    """(シグネチャ, 本体) の組を返す。本体のない宣言（抽象メソッド等）も含める。"""
    i = 0
    while i < len(children):
        c = children[i]
        if c.type in _SIG_TYPES or c.type == "declaration" and _first(c, _SIG_TYPES):
            body = children[i + 1] if i + 1 < len(children) and children[i + 1].type == "function_body" else None
            yield c, body
            i += 2 if body is not None else 1
        else:
            i += 1


def _add(pf, src, sig, body, name, kind, prefix, parent):
    qual = f"{prefix}.{name}" if prefix else name
    end = body if body is not None else sig
    sym = Symbol(
        id=make_symbol_id(pf.path, qual), file=pf.path, name=name, qualname=qual, kind=kind,
        lang="dart", start_line=sig.start_point[0] + 1, end_line=end.end_point[0] + 1,
        signature=" ".join(_text(sig, src).split())[:300], doc=_doc(sig, src), parent=parent,
        calls=_calls(body, src) if body is not None else [],
        exported=not name.startswith("_"),
    )
    pf.symbols.append(sym)
    return sym


def make_skeleton(root, src: bytes) -> str:
    cuts = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "function_body":
            block = _first(n, {"block"})
            if block is not None and block.end_point[0] > block.start_point[0]:
                cuts.append((block.start_byte, block.end_byte))
            continue
        stack.extend(n.children)
    out, pos = [], 0
    for start, end in sorted(cuts):
        out += [src[pos:start], b"{ /* ... */ }"]
        pos = end
    out.append(src[pos:])
    text = b"".join(out).decode("utf-8", errors="replace")
    return "\n".join(line for line in text.splitlines() if line.strip())


def parse_dart(path: str, source: str, sha: str) -> ParsedFile:
    pf = ParsedFile(path=path, lang="dart", sha256=sha, loc=source.count("\n") + 1)
    parser = _get_parser()
    if parser is None:
        pf.error = "tree-sitter-language-pack が未インストールのため解析できません"
        return pf
    src = source.encode("utf-8")
    root = parser.parse(src).root_node
    for node in root.children:
        if node.type == "import_or_export":
            uri = next((n for n in _walk(node) if n.type == "uri"), None)
            if uri is not None:
                pf.imports.append(ImportRef(_text(uri, src).strip("'\""), [], node.start_point[0] + 1))
        elif node.type in _CLASS_TYPES:
            ident = _first(node, {"identifier"})
            name = _text(ident, src) if ident is not None else "anonymous"
            cls = Symbol(
                id=make_symbol_id(path, name), file=path, name=name, qualname=name,
                kind=_CLASS_TYPES[node.type], lang="dart", start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1, doc=_doc(node, src),
                signature=" ".join(_text(node, src).split("{", 1)[0].split())[:300],
                exported=not name.startswith("_"),
            )
            for c in node.children:
                if c.type in ("superclass", "interfaces", "mixins"):
                    cls.bases += [_text(t, src) for t in _walk(c) if t.type == "type_identifier"]
            pf.symbols.append(cls)
            body = _first(node, {"class_body", "enum_body", "extension_body"})
            if body is not None:
                for sig, fbody in _pairs(list(body.children)):
                    mname = _sig_name(sig, src)
                    if mname:
                        _add(pf, src, sig, fbody, mname, "method", name, cls.id)
    for sig, fbody in _pairs(list(root.children)):
        name = _sig_name(sig, src)
        if name:
            _add(pf, src, sig, fbody, name, "function", "", None)
    pf.symbols.sort(key=lambda s: s.start_line)
    if root.has_error:
        pf.error = "構文エラーを含みます（解析できた範囲のみ記録）"
    pf.skeleton = make_skeleton(root, src)
    return pf


def _walk(node):
    """前順（ソース順）で全ノードを返す。"""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))
