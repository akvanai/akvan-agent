"""Persistent Playwright browser session for agent-driven navigation.

Loaded by path from server.py so Docker's slim Playwright image never imports
agent.tools package deps. Keep this module free of agent.tools imports.

Playwright sync API is thread-affine: all browser calls run on one worker thread
even though the HTTP server is threaded.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

MAX_SNAPSHOT_CHARS = 24_000
INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "switch",
    "tab",
    "treeitem",
    "slider",
    "spinbutton",
}


class BrowserSessionError(RuntimeError):
    """Raised for session lifecycle or interaction failures."""


class BrowserSession:
    """One Chromium context shared by the browser runtime process."""

    def __init__(self, *, inactivity_timeout_seconds: int = 900) -> None:
        self.inactivity_timeout_seconds = max(60, int(inactivity_timeout_seconds))
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._profile_name: str | None = None
        self._storage_state_path: str | None = None
        self._refs: dict[str, dict[str, Any]] = {}
        self._last_activity = 0.0
        self._timer: threading.Timer | None = None
        self._jobs: queue.Queue[tuple[Callable[[], Any], queue.Queue[Any]] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, name="akvan-browser", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            fn, reply = item
            try:
                reply.put((True, fn()))
            except BaseException as exc:  # noqa: BLE001 - boundary for HTTP thread
                reply.put((False, exc))

    def _call(self, fn: Callable[[], Any]) -> Any:
        reply: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._jobs.put((fn, reply))
        ok, payload = reply.get()
        if ok:
            return payload
        raise payload

    def status(self) -> dict[str, Any]:
        return self._call(self._status_unlocked)

    def start(
        self,
        *,
        profile: str | None = None,
        storage_state_path: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._start_unlocked(
                profile=profile,
                storage_state_path=storage_state_path,
                url=url,
            )
        )

    def navigate(self, url: str) -> dict[str, Any]:
        return self._call(lambda: self._navigate_unlocked(url))

    def snapshot(self, *, full: bool = False) -> dict[str, Any]:
        return self._call(lambda: self._snapshot_unlocked(full=full))

    def screenshot(self, *, full: bool = True) -> dict[str, Any]:
        return self._call(lambda: self._screenshot_unlocked(full=full))

    def click(self, ref: str) -> dict[str, Any]:
        return self._call(lambda: self._click_unlocked(ref))

    def type_text(self, ref: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        return self._call(lambda: self._type_unlocked(ref, text, submit=submit))

    def scroll(self, direction: str) -> dict[str, Any]:
        return self._call(lambda: self._scroll_unlocked(direction))

    def back(self) -> dict[str, Any]:
        return self._call(self._back_unlocked)

    def press(self, key: str) -> dict[str, Any]:
        return self._call(lambda: self._press_unlocked(key))

    def upload(self, paths: list[str], *, ref: str | None = None) -> dict[str, Any]:
        return self._call(lambda: self._upload_unlocked(paths, ref=ref))

    def close(self, *, save: bool = True) -> dict[str, Any]:
        return self._call(lambda: self._close_and_status(save=save))

    def _status_unlocked(self) -> dict[str, Any]:
        open_session = self._page is not None
        url = ""
        title = ""
        if open_session and self._page is not None:
            try:
                url = self._page.url or ""
                title = self._page.title() or ""
            except Exception:
                pass
        return {
            "ok": True,
            "open": open_session,
            "profile": self._profile_name or "",
            "url": url,
            "title": title,
            "storage_state_path": self._storage_state_path or "",
        }

    def _start_unlocked(
        self,
        *,
        profile: str | None = None,
        storage_state_path: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        self._close_unlocked(save=True)
        state_path = str(storage_state_path or "").strip() or None
        if state_path and not Path(state_path).is_file():
            raise BrowserSessionError(f"Storage state not found: {state_path}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserSessionError(
                "Playwright is required for the browser runtime. Install `akvan-agent[browser]` "
                "and run `playwright install chromium`, or choose Docker mode in `akvan tools`."
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=_headless_default())
        kwargs: dict[str, Any] = {}
        if state_path:
            kwargs["storage_state"] = state_path
        self._context = self._browser.new_context(**kwargs)
        self._page = self._context.new_page()
        self._profile_name = (profile or "").strip() or None
        self._storage_state_path = state_path
        self._refs = {}
        target = (url or "").strip()
        if target:
            self._goto_unlocked(target)
        self._touch_unlocked()
        return self._status_unlocked()

    def _navigate_unlocked(self, url: str) -> dict[str, Any]:
        self._require_page()
        self._goto_unlocked(url)
        self._refs = {}
        self._touch_unlocked()
        return {"ok": True, **self._status_unlocked()}

    def _snapshot_unlocked(self, *, full: bool = False) -> dict[str, Any]:
        page = self._require_page()
        tree = page.accessibility.snapshot() or {}
        lines: list[str] = []
        refs: dict[str, dict[str, Any]] = {}
        counter = {"n": 0}

        def walk(node: dict[str, Any], depth: int) -> None:
            if not isinstance(node, dict):
                return
            role = str(node.get("role") or "").strip() or "generic"
            name = str(node.get("name") or "").strip()
            value = node.get("value")
            interactive = role in INTERACTIVE_ROLES or bool(node.get("focusable"))
            if full or interactive or depth == 0 or name:
                ref = None
                if interactive:
                    counter["n"] += 1
                    ref = f"e{counter['n']}"
                    refs[ref] = {
                        "role": role,
                        "name": name,
                        "value": value,
                    }
                indent = "  " * depth
                label = name or (str(value) if value not in (None, "") else "")
                suffix = f' "{label}"' if label else ""
                ref_suffix = f" [@{ref}]" if ref else ""
                lines.append(f"{indent}{role}{suffix}{ref_suffix}")
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk(child, depth + 1)

        if tree:
            walk(tree, 0)
        else:
            lines.append("(empty accessibility tree)")

        text = "\n".join(lines)
        truncated = False
        if len(text) > MAX_SNAPSHOT_CHARS:
            text = text[: MAX_SNAPSHOT_CHARS - 20] + "\n...[truncated]"
            truncated = True
        self._refs = refs
        self._touch_unlocked()
        status = self._status_unlocked()
        return {
            "ok": True,
            "snapshot": text,
            "element_count": len(refs),
            "truncated": truncated,
            "url": status.get("url", ""),
            "title": status.get("title", ""),
            "profile": status.get("profile", ""),
        }

    def _screenshot_unlocked(self, *, full: bool = True) -> dict[str, Any]:
        import base64

        page = self._require_page()
        png = page.screenshot(
            type="png",
            full_page=bool(full),
            animations="disabled",
        )
        self._touch_unlocked()
        status = self._status_unlocked()
        viewport = page.viewport_size or {}
        return {
            "ok": True,
            "png_base64": base64.b64encode(png).decode("ascii"),
            "full": bool(full),
            "width": viewport.get("width"),
            "height": viewport.get("height"),
            "url": status.get("url", ""),
            "title": status.get("title", ""),
            "profile": status.get("profile", ""),
        }

    def _click_unlocked(self, ref: str) -> dict[str, Any]:
        page = self._require_page()
        target = self._resolve_ref(ref)
        locator = self._locator_for(page, target)
        locator.first.click(timeout=15000)
        self._refs = {}
        self._touch_unlocked()
        return {"ok": True, "clicked": ref, **self._status_unlocked()}

    def _type_unlocked(self, ref: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        page = self._require_page()
        target = self._resolve_ref(ref)
        locator = self._locator_for(page, target)
        locator.first.click(timeout=10000)
        try:
            locator.first.fill(text, timeout=10000)
        except Exception:
            page.keyboard.type(text)
        if submit:
            page.keyboard.press("Enter")
        self._refs = {}
        self._touch_unlocked()
        return {"ok": True, "typed": True, "ref": ref, **self._status_unlocked()}

    def _scroll_unlocked(self, direction: str) -> dict[str, Any]:
        page = self._require_page()
        delta = 800 if direction.lower() == "down" else -800
        page.mouse.wheel(0, delta)
        self._touch_unlocked()
        return {"ok": True, "scrolled": direction, **self._status_unlocked()}

    def _back_unlocked(self) -> dict[str, Any]:
        page = self._require_page()
        page.go_back(wait_until="domcontentloaded", timeout=30000)
        self._refs = {}
        self._touch_unlocked()
        return {"ok": True, **self._status_unlocked()}

    def _press_unlocked(self, key: str) -> dict[str, Any]:
        page = self._require_page()
        page.keyboard.press(key)
        self._refs = {}
        self._touch_unlocked()
        return {"ok": True, "key": key, **self._status_unlocked()}

    def _upload_unlocked(self, paths: list[str], *, ref: str | None = None) -> dict[str, Any]:
        page = self._require_page()
        resolved: list[str] = []
        for raw in paths:
            path = Path(str(raw)).expanduser()
            if not path.is_file():
                raise BrowserSessionError(f"Media file not found: {path}")
            resolved.append(str(path.resolve()))
        if not resolved:
            raise BrowserSessionError("At least one file path is required.")

        method: str | None = None
        ref_key = str(ref or "").strip()
        if ref_key:
            target = self._resolve_ref(ref_key)
            locator = self._locator_for(page, target)
            try:
                with page.expect_file_chooser(timeout=5000) as chooser_info:
                    locator.first.click(timeout=15000)
                chooser_info.value.set_files(resolved)
                method = "file_chooser"
            except Exception as exc:
                raise BrowserSessionError(
                    f"File chooser did not open for ref @{ref_key.lstrip('@')}. "
                    "Re-snapshot and use a media/attach control, or omit ref to use a "
                    f"hidden file input. Last error: {exc}"
                ) from exc
        else:
            for selector, label in (
                ("input[data-testid='fileInput']", "x_file_input"),
                ("input[type='file']", "file_input"),
            ):
                locator = page.locator(selector)
                try:
                    if locator.count() < 1:
                        continue
                except Exception:
                    continue
                locator.first.set_input_files(resolved)
                method = label
                break
            if method is None:
                raise BrowserSessionError(
                    "No file input found on the page. Call browser_snapshot and pass "
                    "ref= for the media/attach button so a file chooser can open."
                )

        self._refs = {}
        self._touch_unlocked()
        return {
            "ok": True,
            "paths": resolved,
            "method": method,
            **self._status_unlocked(),
        }

    def _close_and_status(self, *, save: bool) -> dict[str, Any]:
        self._close_unlocked(save=save)
        return {"ok": True, "open": False}

    def _resolve_ref(self, ref: str) -> dict[str, Any]:
        key = str(ref or "").strip().lstrip("@")
        if not key:
            raise BrowserSessionError("Element ref is required (e.g. e3 or @e3).")
        target = self._refs.get(key)
        if not target:
            raise BrowserSessionError(
                f"Unknown ref @{key}. Call browser_snapshot again; refs are only valid "
                "for the latest snapshot."
            )
        return target

    def _locator_for(self, page: Any, target: dict[str, Any]) -> Any:
        role = str(target.get("role") or "").strip() or "generic"
        name = str(target.get("name") or "").strip()
        if name:
            try:
                return page.get_by_role(role, name=name)
            except Exception:
                pass
            return page.get_by_text(name)
        return page.get_by_role(role)

    def _goto_unlocked(self, url: str) -> None:
        page = self._require_page()
        target = str(url or "").strip()
        if not target:
            raise BrowserSessionError("URL is required.")
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https", "about", "data"}:
            raise BrowserSessionError("Only http(s) URLs are allowed.")
        page.goto(target, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        # Give SPA shells a moment to replace the loading placeholder.
        try:
            page.wait_for_timeout(1500)
        except Exception:
            time.sleep(1.5)

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserSessionError(
                "No browser session is open. Call browser_start first."
            )
        return self._page

    def _touch_unlocked(self) -> None:
        self._last_activity = time.monotonic()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(
            self.inactivity_timeout_seconds, self._idle_close
        )
        self._timer.daemon = True
        self._timer.start()

    def _idle_close(self) -> None:
        try:
            self._call(lambda: self._idle_close_unlocked())
        except Exception:
            pass

    def _idle_close_unlocked(self) -> None:
        if self._page is None:
            return
        if time.monotonic() - self._last_activity < self.inactivity_timeout_seconds - 1:
            return
        self._close_unlocked(save=True)

    def _close_unlocked(self, *, save: bool) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        try:
            if save and self._context is not None and self._storage_state_path:
                path = Path(self._storage_state_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                self._context.storage_state(path=str(path))
        except Exception:
            pass
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._refs = {}
        self._profile_name = None
        self._storage_state_path = None


_SESSION: BrowserSession | None = None
_SESSION_LOCK = threading.Lock()


def get_session(*, inactivity_timeout_seconds: int = 900) -> BrowserSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = BrowserSession(
                inactivity_timeout_seconds=inactivity_timeout_seconds
            )
        else:
            _SESSION.inactivity_timeout_seconds = max(
                60, int(inactivity_timeout_seconds)
            )
        return _SESSION


def _headless_default() -> bool:
    configured = os.getenv("AKVAN_BROWSER_HEADLESS", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return not bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
