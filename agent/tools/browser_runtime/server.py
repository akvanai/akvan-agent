"""Bundled browser runtime for Akvan browser-backed tools."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

def _load_sibling(module_filename: str, module_name: str) -> ModuleType:
    """Load a sibling module by path without importing agent.tools package deps.

    Docker runs this file as a script inside a slim Playwright image. Importing
    `agent.tools...` would pull approval/config/dotenv and fail.
    """

    path = Path(__file__).resolve().parent / module_filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {module_filename} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_banner_renderer():
    return _load_sibling("banner_renderer.py", "akvan_banner_renderer").render_banner_payload


_SESSION_OPS: ModuleType | None = None


def _load_session_ops() -> ModuleType:
    """Load session_ops once so the persistent BrowserSession survives across requests."""

    global _SESSION_OPS
    if _SESSION_OPS is None:
        _SESSION_OPS = _load_sibling("session_ops.py", "akvan_session_ops")
    return _SESSION_OPS


class RuntimeHandler(BaseHTTPRequestHandler):
    profiles_dir: Path | None = None
    inactivity_timeout_seconds = 900
    runtime_name = "akvan-runtime"

    server_version = "AkvanBrowserRuntime/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"ok": True, "runtime": self.runtime_name})
            return
        if self.path == "/browser/status":
            self._handle_browser_status()
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_json()
        if self.path == "/browser/start":
            self._handle_browser_start(payload)
            return
        if self.path == "/browser/navigate":
            self._handle_browser_navigate(payload)
            return
        if self.path == "/browser/snapshot":
            self._handle_browser_snapshot(payload)
            return
        if self.path == "/browser/screenshot":
            self._handle_browser_screenshot(payload)
            return
        if self.path == "/browser/click":
            self._handle_browser_click(payload)
            return
        if self.path == "/browser/type":
            self._handle_browser_type(payload)
            return
        if self.path == "/browser/scroll":
            self._handle_browser_scroll(payload)
            return
        if self.path == "/browser/back":
            self._handle_browser_back(payload)
            return
        if self.path == "/browser/press":
            self._handle_browser_press(payload)
            return
        if self.path == "/browser/upload":
            self._handle_browser_upload(payload)
            return
        if self.path == "/browser/close":
            self._handle_browser_close(payload)
            return
        if self.path == "/banner/render":
            self._handle_banner_render(payload)
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def _session(self) -> Any:
        session_ops = _load_session_ops()
        return session_ops.get_session(
            inactivity_timeout_seconds=self.inactivity_timeout_seconds
        )

    def _handle_browser_status(self) -> None:
        try:
            self._send_json(200, self._session().status())
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _resolve_storage_path(self, payload: dict[str, Any]) -> str | None:
        explicit = str(payload.get("storageStatePath") or payload.get("storage_state_path") or "").strip()
        if explicit:
            return explicit
        profile = str(payload.get("profile") or "").strip()
        if not profile:
            return None
        # Prefer host/container path under configured profiles dir.
        root = self.profiles_dir
        if root is None:
            env_root = os.getenv("AKVAN_BROWSER_PROFILES_DIR", "").strip()
            root = Path(env_root) if env_root else None
        if root is None:
            raise RuntimeError(
                "Profile was requested but browser profiles directory is not configured on the runtime."
            )
        path = Path(root) / profile / "storage_state.json"
        return str(path)

    def _handle_browser_start(self, payload: dict[str, Any]) -> None:
        profile = str(payload.get("profile") or "").strip() or None
        url = str(payload.get("url") or "").strip() or None
        try:
            storage_path = self._resolve_storage_path(payload)
            if profile and storage_path and not Path(storage_path).is_file():
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": f"Profile {profile!r} storage state not found at {storage_path}",
                    },
                )
                return
            result = self._session().start(
                profile=profile,
                storage_state_path=storage_path,
                url=url,
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_navigate(self, payload: dict[str, Any]) -> None:
        url = str(payload.get("url") or "").strip()
        try:
            result = self._session().navigate(url)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_snapshot(self, payload: dict[str, Any]) -> None:
        full = bool(payload.get("full") or False)
        try:
            result = self._session().snapshot(full=full)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_screenshot(self, payload: dict[str, Any]) -> None:
        full = payload.get("full")
        if full is None:
            full = True
        try:
            result = self._session().screenshot(full=bool(full))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_click(self, payload: dict[str, Any]) -> None:
        ref = str(payload.get("ref") or "").strip()
        try:
            result = self._session().click(ref)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_type(self, payload: dict[str, Any]) -> None:
        ref = str(payload.get("ref") or "").strip()
        text = str(payload.get("text") or "")
        submit = bool(payload.get("submit") or False)
        try:
            result = self._session().type_text(ref, text, submit=submit)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_scroll(self, payload: dict[str, Any]) -> None:
        direction = str(payload.get("direction") or "down").strip().lower()
        if direction not in {"up", "down"}:
            self._send_json(400, {"ok": False, "error": "direction must be 'up' or 'down'."})
            return
        try:
            result = self._session().scroll(direction)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_back(self, payload: dict[str, Any]) -> None:
        _ = payload
        try:
            result = self._session().back()
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_press(self, payload: dict[str, Any]) -> None:
        key = str(payload.get("key") or "").strip()
        if not key:
            self._send_json(400, {"ok": False, "error": "key is required."})
            return
        try:
            result = self._session().press(key)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_upload(self, payload: dict[str, Any]) -> None:
        ref = str(payload.get("ref") or "").strip() or None
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": "files is required (list of {name, content_base64}).",
                },
            )
            return
        try:
            upload_mod = _load_sibling("upload_paths.py", "akvan_upload_paths")
            dest = Path(tempfile.gettempdir()) / "akvan-browser-upload" / uuid.uuid4().hex
            paths = upload_mod.materialize_upload_files(files, dest_dir=dest)
            result = self._session().upload(paths, ref=ref)
            result = {
                **result,
                "files": [
                    str(item.get("name") or "")
                    for item in files
                    if isinstance(item, dict)
                ],
            }
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "UploadPathError":
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_browser_close(self, payload: dict[str, Any]) -> None:
        save = payload.get("save")
        if save is None:
            save = True
        try:
            result = self._session().close(save=bool(save))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_banner_render(self, payload: dict[str, Any]) -> None:
        try:
            render_banner_payload = _load_banner_renderer()
            result = render_banner_payload(payload)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": "invalid_banner", "message": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - serialize the runtime boundary.
            self._send_json(500, {"ok": False, "error": "banner_render_failed", "message": str(exc)})
            return
        self._send_json(200, result)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Akvan's browser runtime.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=49733)
    parser.add_argument(
        "--profiles-dir",
        default=os.getenv("AKVAN_BROWSER_PROFILES_DIR", ""),
    )
    parser.add_argument(
        "--inactivity-timeout",
        type=int,
        default=int(os.getenv("AKVAN_BROWSER_INACTIVITY_TIMEOUT", "900")),
    )
    parser.add_argument("--runtime", default=os.getenv("AKVAN_BROWSER_RUNTIME_NAME", "akvan-local"))
    args = parser.parse_args(argv)

    RuntimeHandler.profiles_dir = (
        Path(args.profiles_dir).expanduser() if args.profiles_dir else None
    )
    RuntimeHandler.inactivity_timeout_seconds = max(60, int(args.inactivity_timeout))
    RuntimeHandler.runtime_name = args.runtime
    server = ThreadingHTTPServer((args.host, args.port), RuntimeHandler)
    print(f"Akvan browser runtime listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
