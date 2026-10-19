import pytest

from ai_audit.context.builder import ContextBuilder, ContextTooLarge, number_lines


def count(text):
    return len(text)


def test_priority_and_alternatives():
    b = ContextBuilder(count, 60)
    b.add("req", "", "R" * 10, required=True)
    b.add("src", "S", ["F" * 100, "k" * 20], priority=10)
    b.add("low", "L", "x" * 30, priority=90)
    built = b.build()
    assert built.included == ["req", "src"]
    assert built.degraded == ["src"] and built.dropped == ["low"]
    assert built.text.index("RRR") < built.text.index("kkk")  # 追加順を保つ


def test_required_too_large():
    b = ContextBuilder(count, 5)
    b.add("req", "", "R" * 10, required=True)
    with pytest.raises(ContextTooLarge):
        b.build()


def test_number_lines():
    assert number_lines("a\nb", 9) == " 9| a\n10| b"
