"""Load and encode images for multimodal provider payloads."""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from agent.config import akvan_home

logger = logging.getLogger(__name__)

_RESIZE_TARGET_BYTES = 4 * 1024 * 1024
_MAX_DIMENSION = 7900
_DOWNLOAD_TIMEOUT = 30.0


def screenshots_dir() -> Path:
    """Return the persistent screenshot cache directory."""

    path = akvan_home() / "cache" / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_old_screenshots(*, max_age_hours: int = 24) -> None:
    """Remove screenshot cache files older than ``max_age_hours``."""

    root = screenshots_dir()
    cutoff = time.time() - max_age_hours * 3600
    for path in root.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def sniff_mime(raw: bytes, *, fallback: str = "image/png") -> str:
    """Detect image MIME type from magic bytes."""

    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _resize_if_needed(raw: bytes, mime: str) -> tuple[bytes, str]:
    if len(raw) <= _RESIZE_TARGET_BYTES:
        return raw, mime
    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow unavailable; sending oversized image as-is")
        return raw, mime

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not open image for resize: %s", exc)
        return raw, mime

    width, height = image.size
    scale = 1.0
    longest = max(width, height)
    if longest > _MAX_DIMENSION:
        scale = _MAX_DIMENSION / float(longest)
    # Iteratively shrink until under target size.
    working = image
    out_mime = "image/jpeg" if mime not in {"image/png", "image/webp"} else mime
    for _ in range(8):
        if scale < 1.0:
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            working = image.resize(new_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        save_format = "PNG" if out_mime == "image/png" else "JPEG"
        save_kwargs: dict[str, object] = {}
        if save_format == "JPEG":
            if working.mode not in {"RGB", "L"}:
                working = working.convert("RGB")
            save_kwargs["quality"] = 85
            save_kwargs["optimize"] = True
        working.save(buf, format=save_format, **save_kwargs)
        data = buf.getvalue()
        if len(data) <= _RESIZE_TARGET_BYTES:
            return data, out_mime if save_format == "JPEG" else mime
        scale *= 0.75
    return data, out_mime if save_format == "JPEG" else mime


def load_bytes(source: str) -> tuple[bytes, str]:
    """Load image bytes from a local path or http(s) URL."""

    text = (source or "").strip()
    if not text:
        raise ValueError("Image path or URL is required.")

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            response = client.get(text)
            response.raise_for_status()
            raw = response.content
        mime = sniff_mime(
            raw,
            fallback=response.headers.get("content-type", "image/png").split(";")[0].strip()
            or "image/png",
        )
        return raw, mime

    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")
    raw = path.read_bytes()
    guessed, _ = mimetypes.guess_type(str(path))
    mime = sniff_mime(raw, fallback=guessed or "image/png")
    return raw, mime


def load_image_as_data_url(source: str, *, resize: bool = True) -> str:
    """Return a ``data:<mime>;base64,...`` URL for ``source``."""

    raw, mime = load_bytes(source)
    if resize:
        raw, mime = _resize_if_needed(raw, mime)
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_url_part(data_url: str) -> dict[str, object]:
    """Build an OpenAI-style image_url content part."""

    return {"type": "image_url", "image_url": {"url": data_url}}


def write_png_bytes(raw: bytes, *, prefix: str = "screenshot") -> Path:
    """Persist PNG bytes under the screenshot cache and return the path."""

    cleanup_old_screenshots()
    import uuid

    path = screenshots_dir() / f"{prefix}_{uuid.uuid4().hex}.png"
    path.write_bytes(raw)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
