"""Scheduled job model, schedule parsing, and store operations."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from croniter import croniter

from agent.storage.store import SessionStore

JOB_STATES = frozenset({"scheduled", "paused", "running", "completed"})
DELIVER_LOCAL = "local"
DELIVER_ORIGIN = "origin"

_DURATION_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_CRON_FIELD_RE = re.compile(r"^[\d*\-,/]+$")


@dataclass(frozen=True)
class ScheduleOrigin:
    platform: str
    chat_id: str


@dataclass(frozen=True)
class ParsedSchedule:
    kind: str  # once | interval | cron
    expr: str
    display: str
    next_run_at: float
    minutes: int | None = None  # for interval


@dataclass
class ScheduledJob:
    id: str
    name: str
    prompt: str
    schedule_kind: str
    schedule_expr: str
    schedule_display: str
    enabled: bool
    state: str
    deliver: str
    origin_platform: str | None
    origin_chat_id: str | None
    next_run_at: float | None
    last_run_at: float | None
    last_status: str | None
    last_error: str | None
    repeat_times: int | None
    repeat_completed: int
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule_kind": self.schedule_kind,
            "schedule_expr": self.schedule_expr,
            "schedule_display": self.schedule_display,
            "enabled": self.enabled,
            "state": self.state,
            "deliver": self.deliver,
            "origin_platform": self.origin_platform,
            "origin_chat_id": self.origin_chat_id,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "repeat_times": self.repeat_times,
            "repeat_completed": self.repeat_completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _parse_duration_minutes(text: str) -> int:
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise ValueError(
            f"Invalid duration '{text}'. Use forms like 30m, 2h, or 1d."
        )
    value = int(match.group(1))
    unit = match.group(2).lower()
    if value < 1:
        raise ValueError("Duration must be at least 1.")
    multipliers = {"m": 1, "h": 60, "d": 1440}
    return value * multipliers[unit]


def parse_schedule(schedule: str, *, now: float | None = None) -> ParsedSchedule:
    """Parse a schedule string into kind/expr/display and first next_run_at."""
    text = schedule.strip()
    if not text:
        raise ValueError("Schedule must not be empty.")
    now_ts = time.time() if now is None else now
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    lower = text.lower()

    if lower.startswith("every "):
        minutes = _parse_duration_minutes(text[6:])
        return ParsedSchedule(
            kind="interval",
            expr=str(minutes),
            display=f"every {minutes}m",
            next_run_at=now_ts + minutes * 60,
            minutes=minutes,
        )

    parts = text.split()
    if len(parts) >= 5 and all(_CRON_FIELD_RE.match(p) for p in parts[:5]):
        expr = " ".join(parts[:5] if len(parts) == 5 else parts[:6])
        try:
            iterator = croniter(expr, now_dt)
            next_dt = iterator.get_next(datetime)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid cron expression '{text}': {exc}") from exc
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=timezone.utc)
        return ParsedSchedule(
            kind="cron",
            expr=expr,
            display=expr,
            next_run_at=next_dt.timestamp(),
        )

    if "T" in text or re.match(r"^\d{4}-\d{2}-\d{2}", text):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid ISO timestamp '{text}'.") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ParsedSchedule(
            kind="once",
            expr=dt.astimezone(timezone.utc).isoformat(),
            display=f"once at {dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            next_run_at=dt.timestamp(),
        )

    try:
        minutes = _parse_duration_minutes(text)
    except ValueError as exc:
        raise ValueError(
            f"Unrecognized schedule '{text}'. Use 30m, every 2h, "
            "0 9 * * *, or an ISO timestamp."
        ) from exc
    return ParsedSchedule(
        kind="once",
        expr=str(minutes),
        display=f"once in {minutes}m",
        next_run_at=now_ts + minutes * 60,
        minutes=minutes,
    )


def compute_next_run_at(
    *,
    kind: str,
    expr: str,
    from_time: float | None = None,
) -> float | None:
    """Return the next fire time after from_time, or None for exhausted one-shots."""
    base = time.time() if from_time is None else from_time
    if kind == "once":
        return None
    if kind == "interval":
        minutes = int(expr)
        return base + minutes * 60
    if kind == "cron":
        base_dt = datetime.fromtimestamp(base, tz=timezone.utc)
        iterator = croniter(expr, base_dt)
        next_dt = iterator.get_next(datetime)
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=timezone.utc)
        return next_dt.timestamp()
    raise ValueError(f"Unknown schedule kind '{kind}'.")


def _row_to_job(row: Any) -> ScheduledJob:
    return ScheduledJob(
        id=str(row["id"]),
        name=str(row["name"]),
        prompt=str(row["prompt"]),
        schedule_kind=str(row["schedule_kind"]),
        schedule_expr=str(row["schedule_expr"]),
        schedule_display=str(row["schedule_display"]),
        enabled=bool(row["enabled"]),
        state=str(row["state"]),
        deliver=str(row["deliver"]),
        origin_platform=row["origin_platform"],
        origin_chat_id=row["origin_chat_id"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        repeat_times=row["repeat_times"],
        repeat_completed=int(row["repeat_completed"] or 0),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def create_job(
    store: SessionStore,
    *,
    schedule: str,
    prompt: str,
    name: str | None = None,
    deliver: str | None = None,
    origin: ScheduleOrigin | None = None,
    repeat_times: int | None = None,
    now: float | None = None,
) -> ScheduledJob:
    prompt_text = prompt.strip()
    if not prompt_text:
        raise ValueError("Prompt must not be empty.")
    parsed = parse_schedule(schedule, now=now)
    now_ts = time.time() if now is None else now
    job_id = uuid.uuid4().hex[:12]
    job_name = (name or prompt_text[:48]).strip() or job_id
    if deliver is None:
        deliver_value = DELIVER_ORIGIN if origin is not None else DELIVER_LOCAL
    else:
        deliver_value = deliver.strip() or DELIVER_LOCAL
    row = {
        "id": job_id,
        "name": job_name,
        "prompt": prompt_text,
        "schedule_kind": parsed.kind,
        "schedule_expr": parsed.expr,
        "schedule_display": parsed.display,
        "enabled": 1,
        "state": "scheduled",
        "deliver": deliver_value,
        "origin_platform": origin.platform if origin else None,
        "origin_chat_id": origin.chat_id if origin else None,
        "next_run_at": parsed.next_run_at,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "repeat_times": repeat_times,
        "repeat_completed": 0,
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    store.insert_scheduled_job(row)
    return get_job(store, job_id)  # type: ignore[return-value]


def list_jobs(
    store: SessionStore,
    *,
    include_completed: bool = True,
) -> list[ScheduledJob]:
    rows = store.list_scheduled_jobs(include_completed=include_completed)
    return [_row_to_job(row) for row in rows]


def get_job(store: SessionStore, job_ref: str) -> ScheduledJob | None:
    row = store.get_scheduled_job(job_ref)
    if row is None:
        return None
    return _row_to_job(row)


def resolve_job(store: SessionStore, job_ref: str) -> ScheduledJob:
    job = get_job(store, job_ref.strip())
    if job is None:
        raise ValueError(f"No scheduled job matched '{job_ref}'.")
    return job


def update_job(
    store: SessionStore,
    job_ref: str,
    *,
    schedule: str | None = None,
    prompt: str | None = None,
    name: str | None = None,
    deliver: str | None = None,
    now: float | None = None,
) -> ScheduledJob:
    job = resolve_job(store, job_ref)
    fields: dict[str, Any] = {"updated_at": time.time() if now is None else now}
    if prompt is not None:
        text = prompt.strip()
        if not text:
            raise ValueError("Prompt must not be empty.")
        fields["prompt"] = text
    if name is not None:
        fields["name"] = name.strip() or job.name
    if deliver is not None:
        fields["deliver"] = deliver.strip() or DELIVER_LOCAL
    if schedule is not None:
        parsed = parse_schedule(schedule, now=now)
        fields["schedule_kind"] = parsed.kind
        fields["schedule_expr"] = parsed.expr
        fields["schedule_display"] = parsed.display
        fields["next_run_at"] = parsed.next_run_at
        if job.state == "completed":
            fields["state"] = "scheduled"
            fields["enabled"] = 1
    store.update_scheduled_job(job.id, fields)
    return resolve_job(store, job.id)


def pause_job(store: SessionStore, job_ref: str) -> ScheduledJob:
    job = resolve_job(store, job_ref)
    if job.state == "completed":
        raise ValueError("Completed jobs cannot be paused.")
    store.update_scheduled_job(
        job.id,
        {
            "enabled": 0,
            "state": "paused",
            "updated_at": time.time(),
        },
    )
    return resolve_job(store, job.id)


def resume_job(store: SessionStore, job_ref: str) -> ScheduledJob:
    job = resolve_job(store, job_ref)
    if job.state == "completed":
        raise ValueError("Completed jobs cannot be resumed.")
    next_run = job.next_run_at
    now_ts = time.time()
    if next_run is None or next_run < now_ts:
        recomputed = compute_next_run_at(
            kind=job.schedule_kind,
            expr=job.schedule_expr,
            from_time=now_ts,
        )
        if recomputed is None:
            # Relative one-shot already fired; keep paused semantics by failing.
            if job.schedule_kind == "once":
                raise ValueError("This one-shot job has already fired.")
            next_run = now_ts
        else:
            next_run = recomputed
    store.update_scheduled_job(
        job.id,
        {
            "enabled": 1,
            "state": "scheduled",
            "next_run_at": next_run,
            "updated_at": now_ts,
        },
    )
    return resolve_job(store, job.id)


def remove_job(store: SessionStore, job_ref: str) -> ScheduledJob:
    job = resolve_job(store, job_ref)
    store.delete_scheduled_job(job.id)
    return job


def trigger_job(store: SessionStore, job_ref: str) -> ScheduledJob:
    """Make a job due immediately (next tick will claim it)."""
    job = resolve_job(store, job_ref)
    if job.state == "completed":
        raise ValueError("Completed jobs cannot be run.")
    store.update_scheduled_job(
        job.id,
        {
            "enabled": 1,
            "state": "scheduled",
            "next_run_at": time.time(),
            "updated_at": time.time(),
        },
    )
    return resolve_job(store, job.id)


def claim_due_jobs(
    store: SessionStore,
    *,
    now: float | None = None,
) -> list[ScheduledJob]:
    """Claim due jobs for execution (at-most-once). Advances next_run_at first."""
    now_ts = time.time() if now is None else now
    claimed_rows = store.claim_due_scheduled_jobs(now_ts)
    return [_row_to_job(row) for row in claimed_rows]


def finish_job_run(
    store: SessionStore,
    job: ScheduledJob,
    *,
    success: bool,
    error: str | None = None,
    now: float | None = None,
) -> None:
    now_ts = time.time() if now is None else now
    completed = job.repeat_completed + 1
    # next_run_at was already advanced (or cleared) at claim time.
    current = store.get_scheduled_job(job.id)
    next_run = None if current is None else current["next_run_at"]
    state = "scheduled"
    enabled = 1
    if next_run is None:
        state = "completed"
        enabled = 0
    elif job.repeat_times is not None and completed >= job.repeat_times:
        state = "completed"
        enabled = 0
        next_run = None
    store.update_scheduled_job(
        job.id,
        {
            "state": state,
            "enabled": enabled,
            "next_run_at": next_run,
            "last_run_at": now_ts,
            "last_status": "ok" if success else "error",
            "last_error": None if success else (error or "unknown error"),
            "repeat_completed": completed,
            "updated_at": now_ts,
        },
    )


def format_job_line(job: ScheduledJob) -> str:
    status = job.state
    if not job.enabled and job.state != "completed":
        status = "paused"
    next_run = "—"
    if job.next_run_at is not None:
        next_run = datetime.fromtimestamp(
            job.next_run_at, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"{job.id}  {job.name}  [{status}]  {job.schedule_display}  "
        f"next={next_run}  deliver={job.deliver}"
    )


__all__ = [
    "DELIVER_LOCAL",
    "DELIVER_ORIGIN",
    "JOB_STATES",
    "ParsedSchedule",
    "ScheduleOrigin",
    "ScheduledJob",
    "claim_due_jobs",
    "compute_next_run_at",
    "create_job",
    "finish_job_run",
    "format_job_line",
    "get_job",
    "list_jobs",
    "parse_schedule",
    "pause_job",
    "remove_job",
    "resolve_job",
    "resume_job",
    "trigger_job",
    "update_job",
]
