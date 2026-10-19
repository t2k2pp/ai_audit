from ai_audit.index.parsers import parse_file, parse_source


def _ids(pf):
    return {s.id: s for s in pf.symbols}


def test_python_symbols_unique_and_nested():
    src = (
        "class A:\n    def __init__(self):\n        self.x = helper()\n"
        "class B:\n    def __init__(self):\n        pass\n"
        "def helper():\n    return 1\n"
    )
    pf = parse_source("pkg/m.py", src)
    ids = _ids(pf)
    # B1: 別クラスの同名メソッドが衝突しない
    assert "pkg/m.py::A.__init__" in ids and "pkg/m.py::B.__init__" in ids
    assert len(ids) == len(pf.symbols) == 5
    init = ids["pkg/m.py::A.__init__"]
    assert init.kind == "method" and init.parent == "pkg/m.py::A"
    assert [c.name for c in init.calls] == ["helper"]
    # B2: クラスはメソッドの呼び出しを重複して持たない
    assert ids["pkg/m.py::A"].calls == []


def test_python_imports_and_syntax_error():
    pf = parse_source("a.py", "from .models import Cart, Item\nimport os.path\n")
    assert [(i.module, i.level) for i in pf.imports] == [("models", 1), ("os.path", 0)]
    assert parse_source("b.py", "def (:\n").error.startswith("SyntaxError")


def test_typescript(sample_project):
    pf = parse_file(str(sample_project / "web/api.ts"), str(sample_project))
    ids = _ids(pf)
    assert ids["web/api.ts::ItemApi.fetchItem"].signature == "async fetchItem(id: string): Promise<string>"
    assert "formatPrice" in [c.name for c in ids["web/api.ts::ItemApi.fetchItem"].calls]
    assert ids["web/api.ts::createApi"].kind == "function"
    assert pf.imports[0].module == "./util"
    assert "/* ... */" in pf.skeleton and "res.json" not in pf.skeleton


def test_javascript_and_dart(sample_project):
    js = parse_file(str(sample_project / "web/util.js"), str(sample_project))
    assert {s.qualname for s in js.symbols} == {"formatPrice", "Logger", "Logger.log"}
    dart = parse_file(str(sample_project / "app/counter.dart"), str(sample_project))
    ids = _ids(dart)
    assert ids["app/counter.dart::Counter"].bases == ["Base", "Mix", "Iface"]
    assert ids["app/counter.dart::Counter.inc"].parent == "app/counter.dart::Counter"
    assert [c.name for c in ids["app/counter.dart::add"].calls] == ["helper"]
    assert [i.module for i in dart.imports] == ["package:flutter/material.dart", "util.dart"]
