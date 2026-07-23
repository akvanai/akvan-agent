"""Named Playwright auth profiles for the interactive browser toolset."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import akvan_home
from agent.storage.permissions import ensure_private_dir, ensure_private_file, is_under_akvan_home
from agent.tools.browser_runtime.config import (
    browser_config,
    profiles_dir,
    x_account_legacy_auth_path,
)

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
STORAGE_STATE_NAME = "storage_state.json"
META_NAME = "meta.json"


class ProfileError(ValueError):
    """Invalid profile name, path, or storage-state payload."""


def validate_profile_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not PROFILE_NAME_RE.fullmatch(normalized):
        raise ProfileError(
            "Profile name must be 1-64 chars: letters, digits, underscore, or hyphen "
            "(must start with a letter or digit)."
        )
    return normalized


def profile_dir(name: str, *, project_root: Path | None = None) -> Path:
    return profiles_dir(project_root=project_root) / validate_profile_name(name)


def storage_state_path(name: str, *, project_root: Path | None = None) -> Path:
    return profile_dir(name, project_root=project_root) / STORAGE_STATE_NAME


def meta_path(name: str, *, project_root: Path | None = None) -> Path:
    return profile_dir(name, project_root=project_root) / META_NAME


def is_valid_storage_state(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    cookies = data.get("cookies")
    return isinstance(cookies, list)


def load_storage_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Could not read storage state: {exc}") from exc
    if not is_valid_storage_state(data):
        raise ProfileError(
            "Invalid Playwright storage state. Expected JSON with a top-level cookies array."
        )
    return data


def write_meta(
    name: str,
    *,
    source: str,
    start_url: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "name": validate_profile_name(name),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if start_url:
        meta["start_url"] = start_url
    path = meta_path(name, project_root=project_root)
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if is_under_akvan_home(path):
        ensure_private_file(path)
    return meta


def read_meta(name: str, *, project_root: Path | None = None) -> dict[str, Any]:
    path = meta_path(name, project_root=project_root)
    if not path.is_file():
        return {"name": validate_profile_name(name)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"name": validate_profile_name(name)}
    return data if isinstance(data, dict) else {"name": validate_profile_name(name)}


def import_storage_state(
    name: str,
    source_path: Path | str,
    *,
    source: str = "import",
    start_url: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Validate and copy a storage-state file into a managed profile directory."""

    profile = validate_profile_name(name)
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise ProfileError(f"Auth file not found: {src}")
    load_storage_state(src)

    dest_dir = profile_dir(profile, project_root=project_root)
    ensure_private_dir(dest_dir)
    dest = dest_dir / STORAGE_STATE_NAME
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    if is_under_akvan_home(dest):
        ensure_private_file(dest)
    meta = write_meta(profile, source=source, start_url=start_url, project_root=project_root)
    return {
        "ok": True,
        "name": profile,
        "storage_state_path": str(dest),
        "meta": meta,
    }


def delete_profile(name: str, *, project_root: Path | None = None) -> dict[str, Any]:
    profile = validate_profile_name(name)
    dest_dir = profile_dir(profile, project_root=project_root)
    if not dest_dir.exists():
        return {"ok": True, "name": profile, "deleted": False, "message": "Profile does not exist."}
    shutil.rmtree(dest_dir)
    return {"ok": True, "name": profile, "deleted": True}


def profile_status(name: str, *, project_root: Path | None = None) -> dict[str, Any]:
    profile = validate_profile_name(name)
    state_path = storage_state_path(profile, project_root=project_root)
    ready = state_path.is_file()
    status: dict[str, Any] = {
        "ok": ready,
        "name": profile,
        "ready": ready,
        "auth_file_exists": ready,
        "storage_state_path": str(state_path),
        "meta": read_meta(profile, project_root=project_root),
    }
    if not ready:
        status["message"] = (
            f"Profile {profile!r} has no storage state. "
            "Run `akvan tools` and import an auth file (required on VPS / headless hosts)."
        )
    return status


def list_profiles(*, project_root: Path | None = None) -> list[dict[str, Any]]:
    root = profiles_dir(project_root=project_root)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            items.append(profile_status(child.name, project_root=project_root))
        except ProfileError:
            continue
    return items


def migrate_legacy_x_auth(*, project_root: Path | None = None) -> dict[str, Any] | None:
    """Import legacy ~/.akvan/x/auth.json as profile `x` when missing."""

    legacy = x_account_legacy_auth_path(project_root=project_root)
    if not legacy.is_file():
        return None
    try:
        validate_profile_name("x")
    except ProfileError:
        return None
    if storage_state_path("x", project_root=project_root).is_file():
        return None
    try:
        return import_storage_state(
            "x",
            legacy,
            source="migrated",
            start_url="https://x.com",
            project_root=project_root,
        )
    except ProfileError:
        return None


def ensure_profiles_ready(*, project_root: Path | None = None) -> Path:
    """Ensure profiles directory exists and apply one-shot legacy migration."""

    root = profiles_dir(project_root=project_root)
    ensure_private_dir(root)
    migrate_legacy_x_auth(project_root=project_root)
    return root


def resolve_profile_storage_path(
    name: str | None,
    *,
    project_root: Path | None = None,
) -> Path | None:
    """Return host storage-state path for a profile, or None when anonymous."""

    if not name:
        return None
    path = storage_state_path(name, project_root=project_root)
    if not path.is_file():
        raise ProfileError(
            f"Profile {validate_profile_name(name)!r} is not ready. "
            "Import a Playwright storage state with `akvan tools`."
        )
    return path


def container_storage_path(name: str, *, container_profiles_dir: str) -> str:
    """Map a profile name to the path inside the Docker runtime container."""

    profile = validate_profile_name(name)
    return f"{container_profiles_dir.rstrip('/')}/{profile}/{STORAGE_STATE_NAME}"


def display_available() -> bool:
    """True when a local GUI display is likely available for interactive login."""

    import os
    import sys

    if os.getenv("AKVAN_BROWSER_FORCE_HEADED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32":
        return True
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


class InteractiveLoginSession:
    """Hold a headed browser open until the user confirms login in the TUI."""

    def __init__(
        self,
        name: str,
        *,
        start_url: str | None = None,
        project_root: Path | None = None,
    ) -> None:
        if not display_available():
            raise ProfileError(
                "No local display detected (VPS/headless). "
                "Create the storage state on a desktop machine and import the file."
            )
        self.profile = validate_profile_name(name)
        self.start_url = (start_url or "").strip() or "about:blank"
        self.project_root = project_root
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProfileError(
                "Playwright is required for interactive login. "
                "Install `akvan-agent[browser]` and run `playwright install chromium`."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        if self.start_url != "about:blank":
            try:
                self._page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

    def save_and_close(self) -> dict[str, Any]:
        if self._context is None:
            raise ProfileError("Login session was not opened.")
        dest_dir = profile_dir(self.profile, project_root=self.project_root)
        ensure_private_dir(dest_dir)
        dest = dest_dir / STORAGE_STATE_NAME
        self._context.storage_state(path=str(dest))
        self.close()
        if is_under_akvan_home(dest):
            ensure_private_file(dest)
        meta = write_meta(
            self.profile,
            source="login",
            start_url=self.start_url if self.start_url != "about:blank" else None,
            project_root=self.project_root,
        )
        return {"ok": True, "name": self.profile, "storage_state_path": str(dest), "meta": meta}

    def close(self) -> None:
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
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None


def default_profiles_home() -> Path:
    return akvan_home() / "browser" / "profiles"


def browser_enabled(*, project_root: Path | None = None) -> bool:
    return bool(browser_config(project_root=project_root)["enabled"])
