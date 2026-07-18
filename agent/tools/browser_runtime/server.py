"""Bundled browser runtime for Akvan browser-backed tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _load_banner_renderer():
    """Load banner_renderer without importing agent.tools package deps.

    Docker runs this file as a script inside a slim Playwright image. Importing
    `agent.tools...` would pull approval/config/dotenv and fail. Load the sibling
    module by path instead.
    """

    import importlib.util

    path = Path(__file__).resolve().parent / "banner_renderer.py"
    spec = importlib.util.spec_from_file_location("akvan_banner_renderer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load banner renderer from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_banner_payload


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
            self._send_json(200, x_auth_status(self.auth_state_path, runtime=self.runtime_name))
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
            result = post_to_x(text=text, media_path=media_path, auth_state_path=self.auth_state_path)
        except Exception as exc:  # noqa: BLE001 - runtime boundary should serialize errors.
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, result)

    def _handle_x_fetch_profile(self, payload: dict[str, Any]) -> None:
        username = str(payload.get("username") or "").lstrip("@").strip()
        limit = int(payload.get("limit") or 10)
        try:
            result = fetch_x_profile(username=username, limit=limit, auth_state_path=self.auth_state_path)
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


def x_auth_status(auth_state_path: Path | None, *, runtime: str) -> dict[str, Any]:
    auth_path = Path(auth_state_path).expanduser() if auth_state_path else None
    auth_exists = bool(auth_path and auth_path.is_file())
    status: dict[str, Any] = {
        "ok": auth_exists,
        "configured": bool(auth_path),
        "auth_file_exists": auth_exists,
        "auth_state_path": str(auth_path) if auth_path else "",
        "runtime_ok": True,
        "runtime": runtime,
    }
    if not auth_exists:
        status["message"] = "X auth is not ready. Run `akvan tools` and create ~/.akvan/x/auth.json."
    return status


def post_to_x(*, text: str, media_path: str | None, auth_state_path: Path | None) -> dict[str, Any]:
    auth = x_auth_status(auth_state_path, runtime="akvan-runtime")
    if not auth.get("auth_file_exists"):
        raise RuntimeError(str(auth.get("message")))
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for the browser runtime. Install `akvan-agent[browser]` "
            "and run `playwright install chromium`, or choose Docker mode in `akvan tools`."
        ) from exc

    auth_path = str(auth["auth_state_path"])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=_headless_default())
        context = browser.new_context(storage_state=auth_path)
        page = context.new_page()
        try:
            page.goto(f"https://x.com/intent/tweet?text={quote(text)}", wait_until="domcontentloaded", timeout=45000)
            _fill_post_text(page, text, PlaywrightTimeoutError)
            if media_path:
                _attach_media(page, media_path)
            _click_post(page, PlaywrightTimeoutError)
            time.sleep(2)
        finally:
            context.close()
            browser.close()
    return {"ok": True, "posted": True}


def fetch_x_profile(*, username: str, limit: int, auth_state_path: Path | None) -> dict[str, Any]:
    if not username.replace("_", "").isalnum() or len(username) > 15:
        raise RuntimeError("Invalid X username.")
    auth = x_auth_status(auth_state_path, runtime="akvan-runtime")
    if not auth.get("auth_file_exists"):
        raise RuntimeError(str(auth.get("message")))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for the browser runtime. Install `akvan-agent[browser]` "
            "and run `playwright install chromium`, or choose Docker mode in `akvan tools`."
        ) from exc

    items: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=_headless_default())
        context = browser.new_context(storage_state=str(auth["auth_state_path"]))
        page = context.new_page()
        try:
            page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector("article", timeout=20000)
            articles = page.locator("article").all()[: max(1, min(limit, 50))]
            for article in articles:
                text = article.inner_text(timeout=3000).strip()
                if text:
                    items.append({"text": text})
        finally:
            context.close()
            browser.close()
    return {"ok": True, "items": items}


def _headless_default() -> bool:
    configured = os.getenv("AKVAN_BROWSER_HEADLESS", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return not bool(os.getenv("DISPLAY"))


def _fill_post_text(page: Any, text: str, timeout_error: type[Exception]) -> None:
    """Ensure composer text is set once.

    Navigating to x.com/intent/tweet?text=... already hydrates the draft. Calling
    fills() on top of that can duplicate the caption, exceed X's limit, and leave
    the Post button permanently aria-disabled.
    """

    selectors = ["[data-testid='tweetTextarea_0']", "div[role='textbox']"]
    locator = None
    for selector in selectors:
        try:
            candidate = page.locator(selector).first
            candidate.wait_for(timeout=10000)
            locator = candidate
            break
        except timeout_error:
            continue
        except Exception:
            continue
    if locator is None:
        return

    # Wait for intent-URL hydration before deciding whether to type.
    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector("[data-testid='tweetTextarea_0'], div[role='textbox']");
                return !!(el && (el.innerText || '').trim().length > 0);
            }""",
            timeout=10000,
        )
    except Exception:
        pass

    try:
        current = locator.inner_text(timeout=2000).strip()
    except Exception:
        current = ""

    if current:
        # Intent URL already populated the composer; rewriting duplicates text.
        return

    try:
        locator.fill(text)
    except Exception:
        try:
            locator.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            locator.fill(text)
        except Exception:
            return


def _resolve_media_path(media_path: str) -> Path:
    """Resolve media paths for host and Docker-mounted runtimes."""

    path = Path(media_path).expanduser()
    if path.is_file():
        return path
    # Docker browser runtime mounts the project at /app.
    mounted_root = Path("/app")
    candidates = [mounted_root / path.name]
    parts = path.parts
    if "akvan-agent" in parts:
        idx = parts.index("akvan-agent")
        candidates.append(mounted_root.joinpath(*parts[idx + 1 :]))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return path


def _attach_media(page: Any, media_path: str) -> None:
    path = _resolve_media_path(media_path)
    if not path.is_file():
        raise RuntimeError(f"Media file not found: {media_path}")

    # Prefer X's compose file input; a generic input[type=file] can accept files
    # without attaching a preview to the tweet draft.
    file_input = page.locator("input[data-testid='fileInput']").first
    try:
        file_input.wait_for(state="attached", timeout=15000)
    except Exception:
        file_input = page.locator("input[type='file']").first

    file_input.set_input_files(str(path))

    # Wait for an actual media preview — Post can already be enabled from text alone.
    preview = page.locator(
        "[data-testid='attachments'], img[src*='blob:'], [aria-label*='Remove media'], [aria-label*='Remove']"
    )
    try:
        preview.first.wait_for(state="visible", timeout=30000)
    except Exception as exc:
        raise RuntimeError(
            "Media file was selected but X did not show an image preview. "
            "The post would have been text-only."
        ) from exc


def _click_post(page: Any, timeout_error: type[Exception]) -> None:
    selectors = ["[data-testid='tweetButton']", "[data-testid='tweetButtonInline']"]
    for selector in selectors:
        try:
            # Wait for this Post control to become enabled (media upload can disable it).
            enabled = page.locator(f"{selector}:not([aria-disabled='true'])")
            try:
                enabled.first.wait_for(state="visible", timeout=15000)
            except Exception:
                continue
            button = enabled.first
            try:
                button.click(timeout=5000)
                return
            except Exception:
                # Media compose overlays often intercept pointer events even when
                # the Post button reports enabled=true.
                button.click(timeout=10000, force=True)
                return
        except timeout_error:
            continue
        except Exception:
            continue
    raise RuntimeError("Could not find the X post button. The X UI may have changed or auth may be expired.")


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
