"""Create, update, and delete user skills under ~/.akvan/skills/."""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from agent.config import akvan_home
from agent.event_log import log_skill
from agent.skills.paths import SKILL_SUPPORT_DIRS
from agent.skills.provenance import is_background_review
from agent.skills.registry import (
    CATEGORY_PATTERN,
    SKILL_NAME_PATTERN,
    _split_frontmatter,
)
from agent.skills.usage import (
    bump_patch,
    find_skill_dir,
    forget,
    is_bundled,
    is_pinned,
    mark_agent_created,
)

MAX_SKILL_CONTENT = 500_000


def skill_manage(
    *,
    action: str,
    name: str = "",
    content: str = "",
    category: str = "general",
    old_string: str = "",
    new_string: str = "",
    file_path: str = "",
    file_content: str = "",
) -> dict[str, Any]:
    action = (action or "").strip().lower()
    name = (name or "").strip()

    handlers = {
        "create": lambda: _create(name, content, category),
        "edit": lambda: _edit(name, content),
        "patch": lambda: _patch(name, old_string, new_string),
        "delete": lambda: _delete(name),
        "write_file": lambda: _write_file(name, file_path, file_content),
        "remove_file": lambda: _remove_file(name, file_path),
    }
    handler = handlers.get(action)
    if handler is None:
        return {
            "success": False,
            "error": (
                f"Unknown action {action!r}. Use: create, edit, patch, delete, "
                "write_file, remove_file."
            ),
        }

    guard = _preflight(action, name)
    if guard is not None:
        return guard

    try:
        result = handler()
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if result.get("success"):
        _post_success(action, name)
    return result


def _preflight(action: str, name: str) -> dict[str, Any] | None:
    if action == "create":
        if not name:
            return {"success": False, "error": "name is required for create."}
        return None
    if not name:
        return {"success": False, "error": f"name is required for {action}."}
    if action in {"edit", "patch", "delete", "write_file", "remove_file"}:
        if is_bundled(name) and is_background_review():
            return {
                "success": False,
                "error": f"'{name}' is a bundled skill — background review cannot modify it.",
            }
        if action == "delete" and is_bundled(name):
            return {
                "success": False,
                "error": f"'{name}' is bundled — delete is not allowed.",
            }
        if action == "delete" and is_pinned(name):
            return {
                "success": False,
                "error": f"'{name}' is pinned — unpin with `akvan skills curator unpin {name}` first.",
            }
        if action != "create" and find_skill_dir(name) is None:
            return {"success": False, "error": f"skill '{name}' not found."}
    return None


def _post_success(action: str, name: str) -> None:
    if action == "create" and is_background_review():
        mark_agent_created(name)
    elif action in {"patch", "edit", "write_file", "remove_file"}:
        bump_patch(name)
    elif action == "delete":
        forget(name)
    log_skill(action, name)


def _user_skills_root() -> Path:
    root = akvan_home() / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_name(name: str) -> None:
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError("name must use lowercase letters, digits, _ or -")


def _validate_category(category: str) -> str:
    category = (category or "general").strip().lower()
    if not CATEGORY_PATTERN.fullmatch(category):
        raise ValueError("category must use lowercase letters, digits, _ or -")
    return category


def _validate_skill_content(content: str) -> None:
    if not content.strip():
        raise ValueError("content must not be empty")
    if len(content) > MAX_SKILL_CONTENT:
        raise ValueError(f"content exceeds {MAX_SKILL_CONTENT} characters")
    try:
        metadata, body = _split_frontmatter(content)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    skill_name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(skill_name, str) or not SKILL_NAME_PATTERN.fullmatch(skill_name):
        raise ValueError("frontmatter name must use lowercase letters, digits, _ or -")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("frontmatter description is required")
    if len(description) > 60:
        raise ValueError("description must be at most 60 characters")
    if not body.strip():
        raise ValueError("skill body must not be empty")


def _create(name: str, content: str, category: str) -> dict[str, Any]:
    _validate_name(name)
    category = _validate_category(category)
    _validate_skill_content(content)
    if find_skill_dir(name) is not None:
        return {"success": False, "error": f"skill '{name}' already exists"}
    dest = _user_skills_root() / category / name
    dest.mkdir(parents=True, exist_ok=False)
    skill_md = dest / "SKILL.md"
    skill_md.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return {
        "success": True,
        "message": f"created skill '{name}' in category '{category}'",
        "path": str(skill_md),
    }


def _edit(name: str, content: str) -> dict[str, Any]:
    _validate_skill_content(content)
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"skill '{name}' not found"}
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return {"success": True, "message": f"updated skill '{name}'"}


def _patch(name: str, old_string: str, new_string: str) -> dict[str, Any]:
    if not old_string:
        raise ValueError("old_string is required for patch")
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"skill '{name}' not found"}
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    if old_string not in text:
        return {"success": False, "error": "old_string not found in SKILL.md"}
    count = text.count(old_string)
    if count > 1:
        return {
            "success": False,
            "error": f"old_string appears {count} times — make it unique",
        }
    updated = text.replace(old_string, new_string, 1)
    _validate_skill_content(updated)
    skill_md.write_text(updated, encoding="utf-8")
    return {
        "success": True,
        "message": f"patched skill '{name}'",
        "_change": {"old": old_string, "new": new_string},
    }


def _delete(name: str) -> dict[str, Any]:
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"skill '{name}' not found"}
    shutil.rmtree(skill_dir)
    return {"success": True, "message": f"deleted skill '{name}'"}


def _normalize_support_path(file_path: str) -> str:
    if not file_path.strip() or "\\" in file_path:
        raise ValueError("file_path is invalid")
    candidate = PurePosixPath(file_path.strip())
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("file_path must stay inside the skill directory")
    top = candidate.parts[0] if candidate.parts else ""
    if top and top not in SKILL_SUPPORT_DIRS and candidate.name != "SKILL.md":
        raise ValueError(
            f"support files must live under {sorted(SKILL_SUPPORT_DIRS)} or be SKILL.md"
        )
    return candidate.as_posix()


def _write_file(name: str, file_path: str, file_content: str) -> dict[str, Any]:
    rel = _normalize_support_path(file_path)
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"skill '{name}' not found"}
    target = skill_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(file_content) > MAX_SKILL_CONTENT:
        raise ValueError(f"file_content exceeds {MAX_SKILL_CONTENT} characters")
    target.write_text(
        file_content if file_content.endswith("\n") else file_content + "\n",
        encoding="utf-8",
    )
    return {"success": True, "message": f"wrote {rel} for skill '{name}'"}


def _remove_file(name: str, file_path: str) -> dict[str, Any]:
    rel = _normalize_support_path(file_path)
    if rel == "SKILL.md":
        raise ValueError("cannot remove SKILL.md — use delete to remove the skill")
    skill_dir = find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"skill '{name}' not found"}
    target = skill_dir / rel
    if not target.is_file():
        return {"success": False, "error": f"file {rel!r} not found"}
    target.unlink()
    return {"success": True, "message": f"removed {rel} from skill '{name}'"}
