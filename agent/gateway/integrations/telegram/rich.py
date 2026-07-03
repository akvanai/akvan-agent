"""Bot API rich-message helpers owned by the Telegram integration."""

from __future__ import annotations

import html
import re
from typing import Any

RICH_MESSAGE_MAX_CHARS = 32768

_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$"
)

_RICH_PROTECTED_REGION_RE = re.compile(
    r"(?:```[^\n]*\n[\s\S]*?```)"
    r"|(?:^[^\n]*\|[^\n]*\n"
    r"[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*"
    r"(?:\n[^\n]*\|[^\n]*)*)",
    re.MULTILINE,
)


def rich_normalize_linebreaks(text: str) -> str:
    """Convert single newlines to Markdown hard breaks for rich rendering."""
    if not text or "\n" not in text:
        return text

    out: list[str] = []
    pos = 0
    for match in _RICH_PROTECTED_REGION_RE.finditer(text):
        prose = text[pos : match.start()]
        out.append(re.sub(r"(?<!\n)\n(?!\n)", "  \n", prose))
        out.append(match.group(0))
        pos = match.end()
    tail = text[pos:]
    out.append(re.sub(r"(?<!\n)\n(?!\n)", "  \n", tail))
    return "".join(out)


def needs_rich_rendering(content: str) -> bool:
    """Return True when plain Telegram text would degrade the markdown."""
    if not content:
        return False
    if any(_TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()):
        return True
    if re.search(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+", content):
        return True
    if re.search(r"(?m)^<details\b|^</details>|^<summary\b|^</summary>", content):
        return True
    if "$$" in content:
        return True
    return False


def has_markdown_formatting(content: str) -> bool:
    """Detect common markdown that sendRichMessage can render natively."""
    if not content:
        return False
    if re.search(r"\*\*.+?\*\*", content, re.DOTALL):
        return True
    if re.search(r"(?<!\*)\*(?!\*)(?:\\.|[^*\\])+(?<!\*)\*(?!\*)", content):
        return True
    if re.search(r"(?m)^#{1,6}\s+\S", content):
        return True
    if re.search(r"(?m)^[\-*+]\s+\S", content):
        return True
    if "```" in content:
        return True
    if re.search(r"`[^`\n]+`", content):
        return True
    if re.search(r"\[[^\]]+\]\([^)]+\)", content):
        return True
    if re.search(r"(?m)^>\s+\S", content):
        return True
    if re.search(r"~~.+?~~", content):
        return True
    return False


def should_use_rich_delivery(content: str, *, rich_messages_enabled: bool) -> bool:
    """Decide whether content should be sent via sendRichMessage."""
    if needs_rich_rendering(content):
        return True
    return bool(rich_messages_enabled and has_markdown_formatting(content))


def markdown_to_telegram_html(content: str) -> str | None:
    """Best-effort HTML for plain send fallback when rich API is unavailable."""
    if not has_markdown_formatting(content) and not needs_rich_rendering(content):
        return None

    parts: list[str] = []
    pos = 0
    for match in re.finditer(r"```(?:[^\n]*)\n([\s\S]*?)```", content):
        parts.append(_inline_markdown_to_html(content[pos : match.start()]))
        parts.append(f"<pre><code>{html.escape(match.group(1))}</code></pre>")
        pos = match.end()
    parts.append(_inline_markdown_to_html(content[pos:]))
    return "".join(parts)


def _inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"(?m)^###### (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^##### (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^#### (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^### (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^## (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^# (.+)$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^>\s?(.+)$", r"<blockquote>\1</blockquote>", escaped)
    return escaped


def content_is_pipe_table_primary(content: str) -> bool:
    """True when pipe tables are the only rich construct in the content."""
    if not content or not any(
        _TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()
    ):
        return False
    if re.search(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+", content):
        return False
    if re.search(r"(?m)^<details\b|^</details>|^<summary\b|^</summary>", content):
        return False
    if "$$" in content:
        return False
    return True


def rich_message_payload(
    content: str, *, skip_entity_detection: bool = False
) -> dict[str, Any]:
    """Build the InputRichMessage object from raw agent markdown."""
    payload: dict[str, Any] = {"markdown": rich_normalize_linebreaks(content)}
    if skip_entity_detection:
        payload["skip_entity_detection"] = True
    return payload


def rich_delivery_enabled(content: str, *, rich_messages_enabled: bool) -> bool:
    """Whether rich delivery is allowed for this payload."""
    return rich_messages_enabled or content_is_pipe_table_primary(content)


def rich_eligible(
    content: str,
    *,
    rich_messages_enabled: bool,
    rich_send_disabled: bool,
    bot_supports_rich: bool,
) -> bool:
    """Capability/content eligibility for rich send or finalize."""
    return bool(
        rich_delivery_enabled(content, rich_messages_enabled=rich_messages_enabled)
        and not rich_send_disabled
        and content
        and content.strip()
        and should_use_rich_delivery(content, rich_messages_enabled=rich_messages_enabled)
        and len(content) <= RICH_MESSAGE_MAX_CHARS
        and bot_supports_rich
    )
