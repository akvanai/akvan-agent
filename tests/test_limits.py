from agent.limits import truncate_text


def test_truncation_is_bounded_and_visible() -> None:
    content = "A" * 100 + "Z" * 100

    truncated = truncate_text(content, 100, label="test source")

    assert len(truncated) == 100
    assert "test source truncated" in truncated
    assert truncated.startswith("A")
    assert truncated.endswith("Z")
