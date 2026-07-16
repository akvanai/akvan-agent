"""Central size limits for prompt and skill content."""

from __future__ import annotations

MAX_SOURCE_CHARS = 64 * 1024
MAX_PROMPT_CHARS = 128 * 1024
MAX_SKILL_CHARS = 256 * 1024
MAX_SKILL_METADATA_CHARS = 64 * 1024
MAX_SKILL_RESOURCES = 128


def truncate_text(text: str, limit: int, *, label: str) -> str:
    """Keep bounded head/tail context with an explicit truncation marker."""

    if len(text) <= limit:
        return text
    notice = f"\n\n[... {label} truncated by {len(text) - limit:,} characters ...]\n\n"
    available = max(0, limit - len(notice))
    head_size = available * 3 // 4
    return text[:head_size] + notice + text[-(available - head_size) :]
