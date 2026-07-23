"""Optional interactive browser tools backed by the shared browser runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.tools.base import Tool, ToolImage, ToolResult
from agent.tools.browser_runtime.client import BrowserRuntimeClient, BrowserRuntimeError
from agent.tools.browser_runtime.config import browser_config, is_docker_browser_runtime
from agent.tools.browser_runtime.profiles import (
    ProfileError,
    ensure_profiles_ready,
    list_profiles,
    profile_status,
    resolve_profile_storage_path,
)
from agent.tools.browser_runtime.upload_paths import UploadPathError, encode_upload_files
from agent.vision.encode import write_png_bytes


def _client(*, project_root: Path | None = None) -> BrowserRuntimeClient:
    return BrowserRuntimeClient(project_root=project_root)


def _dumps(payload: dict[str, Any] | list[Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _runtime_error(exc: BrowserRuntimeError) -> str:
    return _dumps({"ok": False, "error": str(exc)})


def build_browser_tools(*, project_root: Path | None = None) -> tuple[Tool, ...]:
    ensure_profiles_ready(project_root=project_root)
    cfg = browser_config(project_root=project_root)

    def browser_list_profiles() -> str:
        ensure_profiles_ready(project_root=project_root)
        items = list_profiles(project_root=project_root)
        return _dumps(
            {
                "ok": True,
                "profiles": [
                    {
                        "name": item["name"],
                        "ready": item.get("ready"),
                        "source": (item.get("meta") or {}).get("source"),
                        "message": item.get("message"),
                    }
                    for item in items
                ],
                "profiles_dir": str(cfg["profiles_dir"]),
            }
        )

    def browser_auth_status(profile: str | None = None) -> str:
        ensure_profiles_ready(project_root=project_root)
        if profile:
            try:
                return _dumps(profile_status(profile, project_root=project_root))
            except ProfileError as exc:
                return _dumps({"ok": False, "error": str(exc)})
        items = list_profiles(project_root=project_root)
        return _dumps(
            {
                "ok": True,
                "profiles": items,
                "count": len(items),
                "profiles_dir": str(cfg["profiles_dir"]),
                "note": "Never read or reveal storage_state.json contents.",
            }
        )

    def browser_start(profile: str | None = None, url: str | None = None) -> str:
        ensure_profiles_ready(project_root=project_root)
        payload: dict[str, Any] = {}
        if url:
            payload["url"] = url
        if profile:
            try:
                # Validate on host; runtime resolves path from profiles dir
                # (host path locally, container mount in Docker).
                resolve_profile_storage_path(profile, project_root=project_root)
            except ProfileError as exc:
                return _dumps({"ok": False, "error": str(exc)})
            payload["profile"] = profile.strip()
            if not is_docker_browser_runtime(project_root=project_root):
                payload["storageStatePath"] = str(
                    resolve_profile_storage_path(profile, project_root=project_root)
                )
        try:
            result = _client(project_root=project_root).post("/browser/start", payload)
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_navigate(url: str) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/navigate", {"url": url}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_snapshot(full: bool = False) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/snapshot", {"full": bool(full)}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_vision(question: str = "", full: bool = True) -> ToolResult:
        try:
            result = _client(project_root=project_root).post(
                "/browser/screenshot", {"full": bool(full)}
            )
        except BrowserRuntimeError as exc:
            return ToolResult(_runtime_error(exc))
        if not result.get("ok"):
            return ToolResult(_dumps(result if isinstance(result, dict) else {"ok": False}))
        encoded = result.get("png_base64")
        if not isinstance(encoded, str) or not encoded.strip():
            return ToolResult(
                _dumps({"ok": False, "error": "Browser runtime did not return a screenshot."})
            )
        import base64

        try:
            png = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            return ToolResult(
                _dumps({"ok": False, "error": f"Invalid screenshot payload: {exc}"})
            )
        path = write_png_bytes(png, prefix="browser")
        payload = {
            "ok": True,
            "screenshot_path": str(path),
            "question": question or "",
            "full": bool(full),
            "url": result.get("url", ""),
            "title": result.get("title", ""),
            "note": (
                "Screenshot attached for the model. On vision-capable models the "
                "pixels are included in this tool result; otherwise an auxiliary "
                "vision model describes it."
            ),
        }
        return ToolResult(
            _dumps(payload),
            images=(
                ToolImage(
                    path=str(path),
                    mime="image/png",
                    question=question or "",
                ),
            ),
        )

    def browser_click(ref: str) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/click", {"ref": ref}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_type(ref: str, text: str, submit: bool = False) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/type",
                {"ref": ref, "text": text, "submit": bool(submit)},
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_scroll(direction: str = "down") -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/scroll", {"direction": direction}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_back() -> str:
        try:
            result = _client(project_root=project_root).post("/browser/back", {})
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_press(key: str) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/press", {"key": key}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_upload(paths: list[str] | str, ref: str | None = None) -> str:
        try:
            files = encode_upload_files(paths)
        except UploadPathError as exc:
            return _dumps({"ok": False, "error": str(exc)})
        payload: dict[str, Any] = {"files": files}
        if ref:
            payload["ref"] = str(ref).strip()
        try:
            result = _client(project_root=project_root).post("/browser/upload", payload)
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    def browser_close(save: bool = True) -> str:
        try:
            result = _client(project_root=project_root).post(
                "/browser/close", {"save": bool(save)}
            )
        except BrowserRuntimeError as exc:
            return _runtime_error(exc)
        return _dumps(result)

    return (
        Tool(
            name="browser_list_profiles",
            description=(
                "List named browser auth profiles (names and ready status only). "
                "Never read profile storage files."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=browser_list_profiles,
        ),
        Tool(
            name="browser_auth_status",
            description=(
                "Check whether browser auth profiles are ready. "
                "Optional profile name. Never reveal storage file contents."
            ),
            parameters={
                "type": "object",
                "properties": {"profile": {"type": "string"}},
                "additionalProperties": False,
            },
            run=browser_auth_status,
        ),
        Tool(
            name="browser_start",
            description=(
                "Start a persistent Playwright browser session. "
                "Optional profile loads that profile's storage state (cookies). "
                "Optional url navigates immediately. Call browser_snapshot after start."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "profile": {"type": "string"},
                    "url": {"type": "string"},
                },
                "additionalProperties": False,
            },
            run=browser_start,
        ),
        Tool(
            name="browser_navigate",
            description="Navigate the open browser session to a URL. Invalidates prior element refs.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            run=browser_navigate,
        ),
        Tool(
            name="browser_snapshot",
            description=(
                "Get an accessibility-tree snapshot of the current page with @eN refs "
                "for interactive elements. Use refs with browser_click / browser_type. "
                "Refs are only valid until the next navigation or interaction. "
                "For visual inspection (CAPTCHAs, layouts, images), use browser_vision."
            ),
            parameters={
                "type": "object",
                "properties": {"full": {"type": "boolean"}},
                "additionalProperties": False,
            },
            run=browser_snapshot,
        ),
        Tool(
            name="browser_vision",
            description=(
                "Take a screenshot of the current browser page for visual inspection. "
                "On vision-capable models, the screenshot pixels are returned in the "
                "tool result so the model can see the page. On text-only models, an "
                "auxiliary vision model describes the screenshot. Use when the "
                "accessibility snapshot is insufficient (CAPTCHAs, images, complex layouts)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What to look for or answer about the page visually.",
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Capture the full scrollable page (default true).",
                    },
                },
                "additionalProperties": False,
            },
            run=browser_vision,
        ),
        Tool(
            name="browser_click",
            description="Click an element by ref from the latest browser_snapshot (e.g. e3 or @e3).",
            parameters={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
            run=browser_click,
        ),
        Tool(
            name="browser_type",
            description="Type text into an element by ref from the latest browser_snapshot.",
            parameters={
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean"},
                },
                "required": ["ref", "text"],
                "additionalProperties": False,
            },
            run=browser_type,
        ),
        Tool(
            name="browser_scroll",
            description="Scroll the page up or down.",
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"]},
                },
                "additionalProperties": False,
            },
            run=browser_scroll,
        ),
        Tool(
            name="browser_back",
            description="Go back in browser history.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=browser_back,
        ),
        Tool(
            name="browser_press",
            description="Press a keyboard key in the browser (e.g. Enter, Escape, Control+A).",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
            run=browser_press,
        ),
        Tool(
            name="browser_upload",
            description=(
                "Attach one or more local files (images/media) in the open browser page. "
                "Reads host paths and sends file bytes to the runtime (works in Docker "
                "without mounting vault/banners). Prefer vault or ~/.akvan/banners paths. "
                "Optional ref clicks a media/attach control first. Call after the "
                "compose/upload UI is open; then browser_snapshot to confirm a media "
                "preview before submitting."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paths": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        ]
                    },
                    "ref": {"type": "string"},
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
            run=browser_upload,
        ),
        Tool(
            name="browser_close",
            description=(
                "Close the browser session. When a profile was used, saves updated "
                "storage state back to the profile by default."
            ),
            parameters={
                "type": "object",
                "properties": {"save": {"type": "boolean"}},
                "additionalProperties": False,
            },
            run=browser_close,
        ),
    )
