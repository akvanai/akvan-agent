"""Vision tools: analyze local/remote images for the agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from agent.tools.base import Tool, ToolImage, ToolResult
from agent.vision.encode import load_bytes, write_png_bytes


def _resolve_image_source(image: str) -> tuple[str, str]:
    """Return (path_or_url_for_attach, mime) after materializing remote URLs to disk."""

    text = (image or "").strip()
    if not text:
        raise ValueError("image is required")
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        raw, mime = load_bytes(text)
        path = write_png_bytes(raw, prefix="vision")
        return str(path), mime
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")
    raw, mime = load_bytes(str(path))
    return str(path.resolve()), mime


def build_vision_tools() -> tuple[Tool, ...]:
    def vision_analyze(image: str, question: str = "") -> ToolResult:
        try:
            path, mime = _resolve_image_source(image)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            )
        payload = {
            "ok": True,
            "image": path,
            "question": question or "",
            "note": (
                "Image attached for the model. On vision-capable models the pixels "
                "are included in this tool result; otherwise an auxiliary vision "
                "model describes it."
            ),
        }
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, indent=2),
            images=(ToolImage(path=path, mime=mime, question=question or ""),),
        )

    return (
        Tool(
            name="vision_analyze",
            description=(
                "Analyze an image from a local file path or http(s) URL. "
                "On vision-capable models, the image pixels are returned in the "
                "tool result so the model can see them natively. On text-only "
                "models, an auxiliary vision model describes the image as text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Local image path or http(s) URL.",
                    },
                    "question": {
                        "type": "string",
                        "description": "Optional question to focus the analysis.",
                    },
                },
                "required": ["image"],
                "additionalProperties": False,
            },
            run=vision_analyze,
        ),
    )
