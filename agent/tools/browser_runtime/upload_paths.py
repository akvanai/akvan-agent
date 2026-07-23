"""Host-side media encoding for browser_upload (bytes over HTTP)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

MAX_UPLOAD_FILES = 4
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UploadPathError(ValueError):
    """Raised when an upload path cannot be validated or encoded."""


def encode_upload_files(paths: list[str] | str) -> list[dict[str, str]]:
    """Validate host paths and return ``[{name, content_base64}, ...]`` for the runtime."""

    if isinstance(paths, str):
        raw_items = [paths]
    else:
        raw_items = list(paths)
    if not raw_items:
        raise UploadPathError("At least one file path is required.")
    if len(raw_items) > MAX_UPLOAD_FILES:
        raise UploadPathError(f"At most {MAX_UPLOAD_FILES} files can be uploaded at once.")

    encoded: list[dict[str, str]] = []
    for raw in raw_items:
        host = Path(str(raw)).expanduser()
        if not host.is_file():
            raise UploadPathError(f"Media file not found: {host}")
        host = host.resolve()
        size = host.stat().st_size
        if size <= 0:
            raise UploadPathError(f"Media file is empty: {host}")
        if size > MAX_UPLOAD_BYTES:
            raise UploadPathError(
                f"Media file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit: {host}"
            )
        data = host.read_bytes()
        encoded.append(
            {
                "name": host.name,
                "content_base64": base64.b64encode(data).decode("ascii"),
            }
        )
    return encoded


def materialize_upload_files(files: list[dict[str, Any]], *, dest_dir: Path) -> list[str]:
    """Decode base64 file payloads into ``dest_dir`` and return absolute paths.

    Used by the browser runtime (local or Docker) so Playwright can call
    ``set_input_files`` on real filesystem paths.
    """

    if not files:
        raise UploadPathError("files is required.")
    if len(files) > MAX_UPLOAD_FILES:
        raise UploadPathError(f"At most {MAX_UPLOAD_FILES} files can be uploaded at once.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise UploadPathError("Each file entry must be an object.")
        name = Path(str(item.get("name") or f"upload-{index}")).name
        if not name or name in {".", ".."}:
            name = f"upload-{index}"
        raw_b64 = str(item.get("content_base64") or "")
        if not raw_b64:
            raise UploadPathError(f"content_base64 is required for file {name!r}.")
        try:
            data = base64.b64decode(raw_b64, validate=True)
        except Exception as exc:
            raise UploadPathError(f"Invalid base64 for file {name!r}.") from exc
        if not data:
            raise UploadPathError(f"Decoded file is empty: {name}")
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadPathError(
                f"Decoded file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit: {name}"
            )
        target = dest_dir / f"{index:02d}-{name}"
        target.write_bytes(data)
        paths.append(str(target.resolve()))
    return paths
