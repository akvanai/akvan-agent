"""Playwright-backed HTML/CSS banner screenshot rendering."""

from __future__ import annotations

import base64
from typing import Any


def render_banner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    html = str(payload.get("html") or "").strip()
    css = str(payload.get("css") or "").strip()
    try:
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Banner width and height must be integers.") from exc
    if not html:
        raise ValueError("Banner HTML is required.")
    if not css:
        raise ValueError("Banner CSS is required.")
    if not 100 <= width <= 4096 or not 100 <= height <= 4096:
        raise ValueError("Banner width and height must be between 100 and 4096 pixels.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for banner rendering. Install `akvan-agent[browser]` "
            "and run `playwright install chromium`, or use Docker runtime mode."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            java_script_enabled=False,
            device_scale_factor=1,
        )
        page = context.new_page()
        page.route("**/*", lambda route: route.abort())
        try:
            page.set_content(html, wait_until="domcontentloaded")
            page.add_style_tag(content=css)
            page.wait_for_timeout(100)
            png = page.screenshot(
                type="png",
                full_page=False,
                animations="disabled",
            )
        finally:
            context.close()
            browser.close()
    return {
        "ok": True,
        "width": width,
        "height": height,
        "png_base64": base64.b64encode(png).decode("ascii"),
    }
