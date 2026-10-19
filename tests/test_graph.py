from ai_audit.index.graph import CodeGraph


def test_graph_build_and_incremental(sample_project):
    g = CodeGraph(str(sample_project))
    stats = g.update()
    assert stats["added"] == len(g.files) and stats["changed"] == 0
    assert g.file_deps["shop/service.py"] == {"shop/models.py"}
    assert g.file_deps["main.py"] == {"shop/service.py"}
    assert g.file_deps["web/api.ts"] == {"web/util.js"}
    assert g.external_deps["shop/service.py"] == set()  # 標準ライブラリは除外

    # 呼び出し解決: checkout → Cart.total（import 先）、self.repo.save → OrderRepository.save
    callees = g.callees("shop/service.py::OrderService.checkout")
    assert "shop/models.py::Cart.total" in callees
    assert "shop/service.py::OrderRepository.save" in callees
    assert "shop/service.py::quick_order" in g.callers("shop/service.py::OrderService.checkout")

    deps = g.module_deps()
    assert deps["(root)"] == {"shop": 1}
    assert g.entry_points() == ["main.py"]
    m = g.module_metrics()
    assert m["shop"]["fan_in"] == 1 and m["(root)"]["instability"] == 1.0

    # 差分更新
    (sample_project / "shop/models.py").write_text("class Item:\n    pass\n", encoding="utf-8")
    (sample_project / "web/util.js").unlink()
    g2 = CodeGraph(str(sample_project))
    stats = g2.update()
    assert stats["changed"] == 1 and stats["removed"] == 1
    assert "shop/models.py::Cart" not in g2.symbols


def test_cycles():
    g = CodeGraph.__new__(CodeGraph)
    assert g.cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}) == [["a", "b", "c"]]


def test_find_symbol(sample_project):
    g = CodeGraph(str(sample_project))
    g.update()
    assert g.find_symbol("OrderService.checkout").id == "shop/service.py::OrderService.checkout"
    assert g.find_symbol("shop/models.py::Cart").kind == "class"
    assert g.find_symbol("nope") is None
    assert "def total" in g.symbol_source("shop/models.py::Cart.total")
