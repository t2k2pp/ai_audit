from ai_audit.usecases.design_doc.facts import build_diagrams, collect_facts, facts_text, update_index


def test_facts_and_diagrams(sample_project):
    g = update_index(str(sample_project), log=lambda *_: None)
    f = collect_facts(g)
    assert f["stats"]["files"] == 7 and f["entry_points"] == ["main.py"]
    assert f["external_deps"] == ["flutter"]
    assert "(root) -> shop(1)" in facts_text(f)
    d = build_diagrams(g, f)
    assert "n__root_ -->|1| n_shop" in d["module_dependencies.mmd"]
    assert "classes_shop.mmd" in d and "+checkout()" in d["classes_shop.mmd"]
