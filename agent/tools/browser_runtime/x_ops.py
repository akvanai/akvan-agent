"""X account Playwright operations for the browser runtime.

Loaded by path from server.py so Docker's slim Playwright image never imports
agent.tools package deps. Keep this module free of agent.tools imports.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


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
