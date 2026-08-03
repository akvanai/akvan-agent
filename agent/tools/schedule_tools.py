"""Agent tool for managing scheduled jobs."""

from __future__ import annotations

import json
from typing import Any

from agent.schedule.jobs import (
    ScheduleOrigin,
    create_job,
    format_job_line,
    list_jobs,
    pause_job,
    remove_job,
    resolve_job,
    resume_job,
    update_job,
)
from agent.storage.store import SessionStore
from agent.tools.base import Tool, ToolResult


def _ok(**payload: Any) -> str:
    return json.dumps({"success": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def schedule_tool(
    *,
    action: str,
    schedule: str | None = None,
    prompt: str | None = None,
    name: str | None = None,
    job_id: str | None = None,
    deliver: str | None = None,
    store: SessionStore | None = None,
    origin: ScheduleOrigin | None = None,
) -> str:
    if store is None:
        return _err("Schedule store is not available in this session.")
    action_name = (action or "").strip().lower()
    try:
        if action_name == "create":
            if not schedule or not prompt:
                return _err("create requires schedule and prompt.")
            job = create_job(
                store,
                schedule=schedule,
                prompt=prompt,
                name=name,
                deliver=deliver,
                origin=origin,
            )
            return _ok(job=job.to_dict(), message=f"Created job {job.id}")
        if action_name == "list":
            jobs = list_jobs(store, include_completed=True)
            return _ok(
                jobs=[job.to_dict() for job in jobs],
                summary="\n".join(format_job_line(job) for job in jobs) or "(none)",
            )
        if action_name == "update":
            if not job_id:
                return _err("update requires job_id.")
            job = update_job(
                store,
                job_id,
                schedule=schedule,
                prompt=prompt,
                name=name,
                deliver=deliver,
            )
            return _ok(job=job.to_dict(), message=f"Updated job {job.id}")
        if action_name == "pause":
            if not job_id:
                return _err("pause requires job_id.")
            job = pause_job(store, job_id)
            return _ok(job=job.to_dict(), message=f"Paused job {job.id}")
        if action_name == "resume":
            if not job_id:
                return _err("resume requires job_id.")
            job = resume_job(store, job_id)
            return _ok(job=job.to_dict(), message=f"Resumed job {job.id}")
        if action_name == "remove":
            if not job_id:
                return _err("remove requires job_id.")
            job = remove_job(store, job_id)
            return _ok(job=job.to_dict(), message=f"Removed job {job.id}")
        if action_name == "run":
            if not job_id:
                return _err("run requires job_id.")
            from agent.schedule.scheduler import run_job_now

            job = resolve_job(store, job_id)
            ok = run_job_now(store, job.id)
            refreshed = resolve_job(store, job.id)
            return _ok(
                job=refreshed.to_dict(),
                ran=ok,
                message=f"Triggered job {job.id}",
            )
        return _err(
            f"Unknown action '{action}'. Use: create, list, update, pause, resume, run, remove."
        )
    except ValueError as exc:
        return _err(str(exc))


SCHEDULE_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "create",
                "list",
                "update",
                "pause",
                "resume",
                "run",
                "remove",
            ],
            "description": "Schedule management action.",
        },
        "schedule": {
            "type": "string",
            "description": (
                "Schedule expression: 30m, every 2h, cron '0 9 * * *', "
                "or an ISO timestamp."
            ),
        },
        "prompt": {
            "type": "string",
            "description": "Self-contained task prompt for the scheduled run.",
        },
        "name": {
            "type": "string",
            "description": "Optional human-readable job name.",
        },
        "job_id": {
            "type": "string",
            "description": "Job id or unique name for update/pause/resume/run/remove.",
        },
        "deliver": {
            "type": "string",
            "description": (
                "Delivery target: local, origin, or telegram:<chat_id>. "
                "Defaults to origin in gateway chats, otherwise local."
            ),
        },
    },
    "required": ["action"],
}


def build_schedule_tools(
    store: SessionStore | None,
    *,
    origin: ScheduleOrigin | None = None,
) -> tuple[Tool, ...]:
    def run(
        action: str,
        schedule: str | None = None,
        prompt: str | None = None,
        name: str | None = None,
        job_id: str | None = None,
        deliver: str | None = None,
    ) -> ToolResult:
        return ToolResult.trusted(
            schedule_tool(
                action=action,
                schedule=schedule,
                prompt=prompt,
                name=name,
                job_id=job_id,
                deliver=deliver,
                store=store,
                origin=origin,
            )
        )

    return (
        Tool(
            name="schedule",
            description=(
                "Create and manage scheduled agent jobs. Jobs run in a fresh "
                "session while the gateway is running. Actions: create, list, "
                "update, pause, resume, run, remove. Schedules: 30m, every 2h, "
                "0 9 * * *, or ISO timestamps."
            ),
            parameters=SCHEDULE_PARAMETERS,
            run=run,
        ),
    )


__all__ = ["SCHEDULE_PARAMETERS", "build_schedule_tools", "schedule_tool"]
