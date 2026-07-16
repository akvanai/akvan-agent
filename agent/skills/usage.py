"""Usage telemetry and provenance for agent-managed skills."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from agent.config import akvan_home
from agent.skills.paths import BUNDLED_MANIFEST

logger = logging.getLogger(__name__)

STATE_ACTIVE = "active"
STATE_ARCHIVED = "archived"
CREATED_BY_AGENT = "agent"


def _skills_dir() -> Path:
    return akvan_home() / "skills"


def _usage_path() -> Path:
    return _skills_dir() / ".usage.json"


def _archive_dir() -> Path:
    return _skills_dir() / ".archive"


def load_usage() -> dict[str, dict[str, Any]]:
    path = _usage_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_usage(data: dict[str, dict[str, Any]]) -> None:
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".usage_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_record(name: str) -> dict[str, Any]:
    return dict(load_usage().get(name, {}))


def _now_ts() -> float:
    return time.time()


def _read_bundled_names() -> set[str]:
    path = _skills_dir() / BUNDLED_MANIFEST
    if not path.is_file():
        return set()
    names: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name = line.split(":", 1)[0].strip()
            if name:
                names.add(name)
    except OSError:
        pass
    return names


def is_bundled(name: str) -> bool:
    return name in _read_bundled_names()


def is_agent_created(name: str) -> bool:
    record = get_record(name)
    return record.get("created_by") == CREATED_BY_AGENT


def mark_agent_created(name: str) -> None:
    data = load_usage()
    rec = data.setdefault(name, {})
    rec["created_by"] = CREATED_BY_AGENT
    rec.setdefault("state", STATE_ACTIVE)
    rec["created_at"] = rec.get("created_at") or _now_ts()
    rec["last_activity_at"] = _now_ts()
    _write_usage(data)


def bump_use(name: str) -> None:
    data = load_usage()
    rec = data.setdefault(name, {})
    rec["use_count"] = int(rec.get("use_count", 0) or 0) + 1
    rec["last_used_at"] = _now_ts()
    rec["last_activity_at"] = _now_ts()
    rec.setdefault("state", STATE_ACTIVE)
    _write_usage(data)


def bump_patch(name: str) -> None:
    data = load_usage()
    rec = data.setdefault(name, {})
    rec["patch_count"] = int(rec.get("patch_count", 0) or 0) + 1
    rec["last_patched_at"] = _now_ts()
    rec["last_activity_at"] = _now_ts()
    rec.setdefault("state", STATE_ACTIVE)
    _write_usage(data)


def forget(name: str) -> None:
    data = load_usage()
    if name in data:
        del data[name]
        _write_usage(data)


def set_pinned(name: str, pinned: bool) -> tuple[bool, str]:
    if is_bundled(name):
        return False, f"'{name}' is a bundled skill and cannot be pinned."
    if pinned and not is_agent_created(name):
        return False, f"'{name}' is not agent-created — only those skills can be pinned."
    data = load_usage()
    rec = data.setdefault(name, {})
    rec["pinned"] = pinned
    _write_usage(data)
    verb = "pinned" if pinned else "unpinned"
    return True, f"{verb} '{name}'"


def is_pinned(name: str) -> bool:
    return bool(get_record(name).get("pinned"))


def find_skill_dir(name: str) -> Path | None:
    """Locate a skill directory under the user skills tree by frontmatter name."""
    root = _skills_dir()
    if not root.is_dir():
        return None
    for skill_md in root.rglob("SKILL.md"):
        if skill_md.is_symlink() or skill_md.parent.is_symlink():
            continue
        rel = skill_md.relative_to(root)
        if len(rel.parts) != 3:
            continue
        try:
            from agent.skills.registry import _split_frontmatter

            text = skill_md.read_text(encoding="utf-8")[:4000]
            metadata, _ = _split_frontmatter(text)
            skill_name = metadata.get("name")
            if skill_name == name or skill_md.parent.name == name:
                return skill_md.parent
        except (OSError, ValueError):
            if skill_md.parent.name == name:
                return skill_md.parent
    return None


def archive_skill(name: str) -> tuple[bool, str]:
    if is_bundled(name):
        return False, f"'{name}' is bundled and cannot be archived."
    if is_pinned(name):
        return False, f"'{name}' is pinned — unpin first."
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return False, f"skill '{name}' not found"
    archive_root = _archive_dir()
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / name
    if dest.exists():
        suffix = 1
        while dest.with_name(f"{name}-{suffix}").exists():
            suffix += 1
        dest = dest.with_name(f"{name}-{suffix}")
    shutil.move(str(skill_dir), str(dest))
    data = load_usage()
    rec = data.setdefault(name, {})
    rec["state"] = STATE_ARCHIVED
    rec["archived_at"] = _now_ts()
    rec["archive_path"] = str(dest)
    _write_usage(data)
    return True, f"archived '{name}'"


def restore_skill(name: str) -> tuple[bool, str]:
    data = load_usage()
    rec = data.get(name, {})
    archive_path = rec.get("archive_path")
    if not archive_path:
        candidate = _archive_dir() / name
        if candidate.is_dir():
            archive_path = str(candidate)
        else:
            return False, f"no archived copy of '{name}' found"
    src = Path(str(archive_path))
    if not src.is_dir():
        return False, f"archive path missing for '{name}'"
    if find_skill_dir(name) is not None:
        return False, f"'{name}' already exists in the active skills tree"
    category = "general"
    skill_md = src / "SKILL.md"
    if skill_md.is_file():
        try:
            from agent.skills.registry import _split_frontmatter

            metadata, _ = _split_frontmatter(skill_md.read_text(encoding="utf-8")[:4000])
            cat = metadata.get("category")
            if isinstance(cat, str) and cat.strip():
                category = cat.strip()
        except (OSError, ValueError):
            pass
    dest = _skills_dir() / category / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    rec = data.setdefault(name, {})
    rec["state"] = STATE_ACTIVE
    rec.pop("archived_at", None)
    rec.pop("archive_path", None)
    rec["last_activity_at"] = _now_ts()
    _write_usage(data)
    return True, f"restored '{name}' to {dest.relative_to(_skills_dir())}"


def list_agent_created() -> list[dict[str, Any]]:
    usage = load_usage()
    bundled = _read_bundled_names()
    result: list[dict[str, Any]] = []
    for name, rec in sorted(usage.items()):
        if rec.get("created_by") != CREATED_BY_AGENT:
            continue
        if name in bundled:
            continue
        result.append({"name": name, **rec})
    return result
