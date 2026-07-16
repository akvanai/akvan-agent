"""Optional X account automation tools backed by the browser runtime."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.tools.base import Tool
from agent.tools.browser_runtime.client import BrowserRuntimeClient, BrowserRuntimeError
from agent.tools.browser_runtime.config import x_account_config

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _auth_status(*, project_root: Path | None = None) -> dict[str, Any]:
    cfg = x_account_config(project_root=project_root)
    auth_path = Path(cfg["auth_state_path"])
    status = {
        "configured": bool(cfg["enabled"]),
        "auth_file_exists": auth_path.is_file(),
        "auth_state_path": str(auth_path),
    }
    if not auth_path.is_file():
        status["ok"] = False
        status["message"] = "X auth is not ready. Run `akvan tools` and create ~/.akvan/x/auth.json."
        return status
    try:
        client = BrowserRuntimeClient(project_root=project_root)
        try:
            runtime = client.get("/x/auth/status")
        except BrowserRuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
            runtime = client.get("/health")
        status.update(runtime)
        status.setdefault("ok", True)
        status.setdefault("runtime_ok", True)
    except BrowserRuntimeError as exc:
        status.update({"ok": False, "runtime_ok": False, "message": str(exc)})
    return status


def build_x_account_tools(*, project_root: Path | None = None) -> tuple[Tool, ...]:
    cfg = x_account_config(project_root=project_root)

    def x_auth_status() -> str:
        return json.dumps(_auth_status(project_root=project_root), ensure_ascii=False, indent=2)

    def x_fetch_profile(username: str, limit: int | None = None) -> str:
        normalized = str(username or "").lstrip("@").strip()
        if not USERNAME_RE.fullmatch(normalized):
            raise ValueError("Invalid X username.")
        auth = _auth_status(project_root=project_root)
        if not auth.get("auth_file_exists"):
            raise ValueError(str(auth.get("message")))
        fetch_limit = max(1, min(int(limit or cfg["default_fetch_limit"]), 50))
        result = BrowserRuntimeClient(project_root=project_root).post(
            "/x/fetch-profile", {"username": normalized, "limit": fetch_limit}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def x_post(text: str, media_path: str | None = None, confirmed: bool = False) -> str:
        if not confirmed:
            raise ValueError("Refusing to post to X without explicit user confirmation.")
        if not str(text or "").strip():
            raise ValueError("Post text is required.")
        auth = _auth_status(project_root=project_root)
        if not auth.get("auth_file_exists"):
            raise ValueError(str(auth.get("message")))
        payload = {"text": text, "mediaPath": media_path or ""}
        result = BrowserRuntimeClient(project_root=project_root).post("/x/post", payload)
        return json.dumps(result, ensure_ascii=False, indent=2)

    return (
        Tool(
            name="x_auth_status",
            description="Check whether X account automation is configured without revealing auth file contents.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=x_auth_status,
        ),
        Tool(
            name="x_fetch_profile",
            description="Fetch recent public posts from an X profile through the configured browser runtime.",
            parameters={
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["username"],
                "additionalProperties": False,
            },
            run=x_fetch_profile,
        ),
        Tool(
            name="x_post",
            description="Post text and optional media to the configured X account after explicit user confirmation.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "media_path": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["text", "confirmed"],
                "additionalProperties": False,
            },
            run=x_post,
        ),
    )
