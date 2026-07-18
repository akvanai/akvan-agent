"""Bundled browser runtime for Akvan browser-backed tools."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
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


def _load_x_ops() -> ModuleType:
    return _load_sibling("x_ops.py", "akvan_x_ops")


class RuntimeHandler(BaseHTTPRequestHandler):
    auth_state_path: Path | None = None
    runtime_name = "akvan-runtime"

    server_version = "AkvanBrowserRuntime/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"ok": True, "runtime": self.runtime_name})
            return
        if self.path == "/x/auth/status":
            x_ops = _load_x_ops()
            self._send_json(200, x_ops.x_auth_status(self.auth_state_path, runtime=self.runtime_name))
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_json()
        if self.path == "/x/post":
            self._handle_x_post(payload)
            return
        if self.path == "/x/fetch-profile":
            self._handle_x_fetch_profile(payload)
            return
        if self.path == "/banner/render":
            self._handle_banner_render(payload)
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

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

    def _handle_x_post(self, payload: dict[str, Any]) -> None:
        text = str(payload.get("text") or "").strip()
        media_path = str(payload.get("mediaPath") or "").strip() or None
        if not text:
            self._send_json(400, {"ok": False, "error": "Post text is required."})
            return
        try:
            x_ops = _load_x_ops()
            result = x_ops.post_to_x(text=text, media_path=media_path, auth_state_path=self.auth_state_path)
        except Exception as exc:  # noqa: BLE001 - runtime boundary should serialize errors.
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_x_fetch_profile(self, payload: dict[str, Any]) -> None:
        username = str(payload.get("username") or "").lstrip("@").strip()
        limit = int(payload.get("limit") or 10)
        try:
            x_ops = _load_x_ops()
            result = x_ops.fetch_x_profile(username=username, limit=limit, auth_state_path=self.auth_state_path)
        except Exception as exc:  # noqa: BLE001 - runtime boundary should serialize errors.
            self._send_json(500, {"ok": False, "error": str(exc)})
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
    parser.add_argument("--auth-state-path", default=os.getenv("AKVAN_X_AUTH_STATE_PATH", ""))
    parser.add_argument("--runtime", default=os.getenv("AKVAN_BROWSER_RUNTIME_NAME", "akvan-local"))
    args = parser.parse_args(argv)

    RuntimeHandler.auth_state_path = Path(args.auth_state_path).expanduser() if args.auth_state_path else None
    RuntimeHandler.runtime_name = args.runtime
    server = ThreadingHTTPServer((args.host, args.port), RuntimeHandler)
    print(f"Akvan browser runtime listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
