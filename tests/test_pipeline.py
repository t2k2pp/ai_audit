import pytest

from ai_audit.pipeline.engine import Pipeline, StepFailed
from ai_audit.pipeline.jobstore import JobStore, stable_hash


def test_step_reuse_and_force(tmp_path):
    store = JobStore(str(tmp_path / "w"), "t")
    calls = []

    def work():
        calls.append(1)
        return {"n": len(calls)}

    p = Pipeline("t", store, quiet=True)
    assert p.step("a/b", {"x": 1}, work) == {"n": 1}
    assert p.step("a/b", {"x": 1}, work) == {"n": 1}      # 再利用
    assert p.step("a/b", {"x": 2}, work) == {"n": 2}      # 入力が変われば再実行
    assert Pipeline("t", store, force=True, quiet=True).step("a/b", {"x": 2}, work) == {"n": 3}
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_map_resumes_after_failure(tmp_path):
    store = JobStore(str(tmp_path / "w"), "t")
    fail = {"b"}
    executed = []

    def fn(item):
        executed.append(item)
        if item in fail:
            raise RuntimeError("boom")
        return item.upper()

    p = Pipeline("t", store, quiet=True)
    assert p.map(["a", "b", "c"], lambda i: i, lambda i: i, fn) == ["A", None, "C"]
    assert p.stats["failed"] == 1
    fail.clear()
    executed.clear()
    p2 = Pipeline("t", store, quiet=True)
    assert p2.map(["a", "b", "c"], lambda i: i, lambda i: i, fn) == ["A", "B", "C"]
    assert executed == ["b"]          # 失敗した要素だけ再実行される
    p2.finish()
    assert store.read_status()["stage"] == "完了"


def test_step_failed():
    import tempfile
    p = Pipeline("t", JobStore(tempfile.mkdtemp(), "t"), quiet=True)
    with pytest.raises(StepFailed):
        p.step("x", {}, lambda: 1 / 0)
