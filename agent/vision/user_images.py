"""Helpers for attaching user-supplied images to a turn."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_EXPLICIT_PATH = re.compile(
    r"(?:^|\s)(?:@|image:|file:)(?P<path>(?:~/|\.{0,2}/)?[^\s]+)",
    re.IGNORECASE,
)


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def extract_image_paths_from_text(text: str) -> tuple[str, tuple[str, ...]]:
    """Detect local image paths in user text and return (display_text, paths).

    Supports:
    - Explicit markers: ``@/path.png``, ``image:/path.png``, ``file:/path.png``
    - Bare existing image file paths as tokens
    """

    raw = text or ""
    found: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        path = Path(candidate).expanduser()
        if not path.is_file() or not is_image_path(path):
            return
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            found.append(resolved)

    remainder = raw
    for match in list(_EXPLICIT_PATH.finditer(raw)):
        _add(match.group("path"))
        remainder = remainder.replace(match.group(0), " ", 1)

    try:
        tokens = shlex.split(remainder)
    except ValueError:
        tokens = remainder.split()

    kept: list[str] = []
    for token in tokens:
        path = Path(token).expanduser()
        if path.is_file() and is_image_path(path):
            _add(token)
            continue
        kept.append(token)

    display = " ".join(kept).strip()
    if not display and found:
        display = "Please examine the attached image(s)."
    return display, tuple(found)
