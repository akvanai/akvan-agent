"""Telegram rich message helper tests."""

from __future__ import annotations

from agent.gateway.integrations.telegram.rich import (
    content_is_pipe_table_primary,
    has_markdown_formatting,
    markdown_to_telegram_html,
    needs_rich_rendering,
    rich_eligible,
    rich_message_payload,
    rich_normalize_linebreaks,
    should_use_rich_delivery,
)


TABLE = "| Case | Status |\n| --- | --- |\n| one | ok |"
RICH_CONTENT = TABLE + "\n\n- [x] done\n\n<details><summary>x</summary>\ny</details>\n\n$$x$$"


def test_rich_normalize_linebreaks_preserves_paragraphs() -> None:
    payload = rich_message_payload("Line 1\nLine 2\n\nParagraph 2")
    assert "Line 1  \nLine 2" in payload["markdown"]
    assert "\n\nParagraph 2" in payload["markdown"]


def test_rich_normalize_linebreaks_preserves_code_blocks() -> None:
    content = "Before\n```\na|b\n---\n```\nAfter"
    normalized = rich_normalize_linebreaks(content)
    assert "```\na|b\n---\n```" in normalized


def test_needs_rich_rendering_detects_tables_and_tasks() -> None:
    assert needs_rich_rendering(TABLE)
    assert needs_rich_rendering("- [x] item")
    assert not needs_rich_rendering("plain prose")


def test_content_is_pipe_table_primary() -> None:
    assert content_is_pipe_table_primary(TABLE)
    assert not content_is_pipe_table_primary(RICH_CONTENT)


def test_rich_eligible_respects_flags() -> None:
    assert rich_eligible(
        TABLE,
        rich_messages_enabled=True,
        rich_send_disabled=False,
        bot_supports_rich=True,
    )
    assert not rich_eligible(
        "plain prose",
        rich_messages_enabled=True,
        rich_send_disabled=False,
        bot_supports_rich=True,
    )
    assert rich_eligible(
        "Hello **world**",
        rich_messages_enabled=True,
        rich_send_disabled=False,
        bot_supports_rich=True,
    )
    assert rich_eligible(
        TABLE,
        rich_messages_enabled=False,
        rich_send_disabled=False,
        bot_supports_rich=True,
    )


def test_has_markdown_formatting_detects_common_syntax() -> None:
    assert has_markdown_formatting("Hello **world**")
    assert has_markdown_formatting("# Title\n\nBody")
    assert has_markdown_formatting("- item one")
    assert not has_markdown_formatting("plain prose")


def test_should_use_rich_delivery_when_rich_messages_enabled() -> None:
    assert should_use_rich_delivery("**bold**", rich_messages_enabled=True)
    assert not should_use_rich_delivery("plain prose", rich_messages_enabled=True)
    assert should_use_rich_delivery(TABLE, rich_messages_enabled=False)


def test_markdown_to_telegram_html_converts_bold() -> None:
    rendered = markdown_to_telegram_html("Hello **world**")
    assert rendered is not None
    assert "<b>world</b>" in rendered
