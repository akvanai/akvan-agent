"""Basic curator for agent-created skills."""

from __future__ import annotations

import time
from typing import Any

from agent.skills.config import CuratorConfig, load_curator_config
from agent.skills.usage import (
    CREATED_BY_AGENT,
    STATE_ACTIVE,
    archive_skill,
    find_skill_dir,
    get_record,
    is_agent_created,
    is_bundled,
    is_pinned,
    list_agent_created,
    load_usage,
    restore_skill,
    set_pinned,
)


def curator_status(*, project_root=None) -> dict[str, Any]:
    agent_created = list_agent_created()
    active = [s for s in agent_created if s.get("state", STATE_ACTIVE) != "archived"]
    archived = [s for s in agent_created if s.get("state") == "archived"]
    return {
        "agent_created": len(agent_created),
        "active": len(active),
        "archived": len(archived),
        "skills": agent_created,
    }


def archive_stale(*, days: int | None = None, project_root=None) -> dict[str, Any]:
    cfg = load_curator_config(project_root=project_root)
    threshold_days = days if days is not None else cfg.archive_after_days
    cutoff = time.time() - threshold_days * 86400
    archived: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in list_agent_created():
        name = entry["name"]
        if entry.get("state") == "archived":
            continue
        if is_pinned(name):
            skipped.append({"name": name, "reason": "pinned"})
            continue
        last = entry.get("last_activity_at") or entry.get("last_used_at") or entry.get(
            "created_at"
        )
        if last is None:
            skipped.append({"name": name, "reason": "no activity timestamp"})
            continue
        try:
            last_ts = float(last)
        except (TypeError, ValueError):
            skipped.append({"name": name, "reason": "invalid timestamp"})
            continue
        if last_ts > cutoff:
            skipped.append({"name": name, "reason": "still active"})
            continue
        ok, message = archive_skill(name)
        if ok:
            archived.append(name)
        else:
            skipped.append({"name": name, "reason": message})
    return {
        "archived": archived,
        "skipped": skipped,
        "threshold_days": threshold_days,
    }


def pin_skill(name: str, *, pinned: bool = True) -> tuple[bool, str]:
    if not is_agent_created(name) and pinned:
        rec = get_record(name)
        if rec.get("created_by") != CREATED_BY_AGENT:
            return False, f"'{name}' is not agent-created — only those skills can be pinned."
    return set_pinned(name, pinned)


def format_status_report(status: dict[str, Any]) -> str:
    lines = [
        "## Curator status",
        "",
        f"- Agent-created skills: {status['agent_created']}",
        f"- Active: {status['active']}",
        f"- Archived: {status['archived']}",
        "",
    ]
    skills = status.get("skills") or []
    if not skills:
        lines.append("No agent-created skills in curator scope.")
        return "\n".join(lines)
    lines.append("| Skill | State | Uses | Pinned |")
    lines.append("| --- | --- | --- | --- |")
    for entry in skills:
        name = entry.get("name", "?")
        state = entry.get("state", STATE_ACTIVE)
        uses = entry.get("use_count", 0)
        pinned = "yes" if entry.get("pinned") else "no"
        lines.append(f"| {name} | {state} | {uses} | {pinned} |")
    return "\n".join(lines)
