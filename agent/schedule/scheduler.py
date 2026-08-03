"""Execute due scheduled jobs and deliver results."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import akvan_home
from agent.schedule.config import load_schedule_config
from agent.schedule.jobs import (
    ScheduledJob,
    claim_due_jobs,
    finish_job_run,
    get_job,
)
from agent.storage.store import SessionStore, open_session_store

logger = logging.getLogger(__name__)

SILENT_MARKER = "[SILENT]"
DeliverySend = Callable[[str, str], Any]


def schedule_output_dir(job_id: str) -> Path:
    text = str(job_id or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"Invalid schedule job id for output path: {job_id!r}")
    path = akvan_home() / "schedule" / "output" / text
    path.mkdir(parents=True, exist_ok=True)
    return path


def schedule_toolsets_for_run(*, project_root: Path | None = None) -> tuple[str, ...]:
    """Default toolsets without the schedule toolset (recursion guard)."""
    from agent.tools.registry import default_enabled_toolsets

    return tuple(
        name
        for name in default_enabled_toolsets(project_root=project_root)
        if name != "schedule"
    )


def _wrap_delivery(job: ScheduledJob, content: str, *, wrap: bool) -> str:
    if not wrap:
        return content
    return (
        f"Scheduled job: {job.name}\n"
        f"(job_id: {job.id})\n"
        f"-------------\n\n"
        f"{content}\n\n"
        f"Manage with /schedule or ask Akvan to pause/remove this job."
    )


def _resolve_telegram_chat_id(job: ScheduledJob) -> str | None:
    deliver = (job.deliver or "").strip()
    if deliver.startswith("telegram:"):
        chat_id = deliver.split(":", 1)[1].strip()
        return chat_id or None
    if deliver == "origin":
        if job.origin_platform == "telegram" and job.origin_chat_id:
            return job.origin_chat_id
        return None
    return None


def _deliver_via_http(chat_id: str, text: str) -> str | None:
    """Fallback Telegram send when the gateway adapter is not live."""
    try:
        from agent.tools.telegram_delivery import load_telegram_delivery_settings
        from agent.tools.telegram_delivery import _send_text  # noqa: PLC2701
    except ImportError:
        return "Telegram delivery module unavailable."
    try:
        settings = load_telegram_delivery_settings()
    except Exception as exc:  # noqa: BLE001
        return f"Telegram delivery not configured: {exc}"
    if not settings.telegram_bot_token:
        return "Telegram bot token is not configured."
    try:
        _send_text(settings, recipient=chat_id, text=text)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def deliver_result(
    job: ScheduledJob,
    content: str,
    *,
    delivery_send: DeliverySend | None = None,
    wrap: bool | None = None,
) -> str | None:
    """Deliver job output. Returns an error string on failure, else None."""
    if content.strip().startswith(SILENT_MARKER):
        return None
    cfg = load_schedule_config()
    should_wrap = cfg.wrap_response if wrap is None else wrap
    body = _wrap_delivery(job, content, wrap=should_wrap)
    deliver = (job.deliver or "local").strip() or "local"
    if deliver == "local":
        return None
    chat_id = _resolve_telegram_chat_id(job)
    if chat_id is None:
        if deliver == "origin":
            logger.info(
                "Job %s: deliver=origin but no resolvable chat — local only",
                job.id,
            )
            return None
        return f"Unsupported deliver target: {deliver}"
    # Prefer live gateway adapter when available.
    if delivery_send is not None:
        try:
            result = delivery_send(chat_id, body)
            # Gateway delivery.send is async; callers pass a sync bridge.
            if hasattr(result, "__await__"):
                return "delivery_send must be a synchronous callable"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Live delivery failed for job %s (%s); trying HTTP fallback",
                job.id,
                exc,
            )
            return _deliver_via_http(chat_id, body)
        return None
    return _deliver_via_http(chat_id, body)


def _write_output(job: ScheduledJob, content: str, *, status: str) -> Path:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = schedule_output_dir(job.id) / f"{stamp}.md"
    path.write_text(
        (
            f"# Scheduled job: {job.name}\n\n"
            f"- **Job ID:** {job.id}\n"
            f"- **Status:** {status}\n"
            f"- **Schedule:** {job.schedule_display}\n"
            f"- **Run time (UTC):** {stamp}\n\n"
            f"---\n\n"
            f"{content}\n"
        ),
        encoding="utf-8",
    )
    return path


def run_job(
    job: ScheduledJob,
    *,
    store: SessionStore,
    delivery_send: DeliverySend | None = None,
    settings=None,
    provider=None,
) -> tuple[bool, str, str | None]:
    """
    Execute one scheduled job in a fresh session.

    Returns (success, final_response_or_error_text, error_message).
    """
    from agent.config import load_settings
    from agent.providers import build_provider
    from agent.providers.base import ProviderError
    from agent.session import AgentSession

    owns_provider = provider is None
    active_settings = settings
    active_provider = provider
    try:
        if active_settings is None:
            active_settings = load_settings(prompt_for_missing_key=False)
        if active_provider is None:
            active_provider = build_provider(active_settings)
        toolsets = schedule_toolsets_for_run()
        session = AgentSession.create(
            provider=active_provider,
            model=active_settings.model,
            max_iterations=active_settings.max_iterations,
            approval_mode=active_settings.approval_mode,
            approval_timeout=active_settings.approval_timeout,
            terminal_timeout=active_settings.terminal_timeout,
            yolo=True,
            store=store,
            session_source="schedule",
            enabled_toolsets=toolsets,
        )
        try:
            answer = session.loop.run_turn(session.messages, job.prompt)
            session.persist_new_messages()
        finally:
            session.end()
        _write_output(job, answer, status="ok")
        delivery_error = deliver_result(job, answer, delivery_send=delivery_send)
        if delivery_error:
            logger.warning("Job %s delivery error: %s", job.id, delivery_error)
        return True, answer, None
    except (ProviderError, ValueError, RuntimeError, OSError) as exc:
        logger.exception("Scheduled job %s failed", job.id)
        message = str(exc)
        _write_output(job, message, status="error")
        alert = f"Scheduled job '{job.name}' failed: {message}"
        deliver_result(job, alert, delivery_send=delivery_send)
        return False, message, message
    finally:
        if owns_provider and active_provider is not None:
            active_provider.close()


def run_one_job(
    job: ScheduledJob,
    *,
    store: SessionStore,
    delivery_send: DeliverySend | None = None,
    settings=None,
    provider=None,
) -> bool:
    success, _output, error = run_job(
        job,
        store=store,
        delivery_send=delivery_send,
        settings=settings,
        provider=provider,
    )
    finish_job_run(store, job, success=success, error=error)
    return success


def tick(
    store: SessionStore | None = None,
    *,
    delivery_send: DeliverySend | None = None,
    settings=None,
    provider=None,
    now: float | None = None,
) -> int:
    """Claim and run all due jobs serially. Returns number of jobs executed."""
    owns_store = store is None
    active = store or open_session_store()
    if active is None:
        logger.error("Schedule tick skipped — session store unavailable.")
        return 0
    try:
        due = claim_due_jobs(active, now=now)
        if not due:
            return 0
        logger.info("Schedule tick: %d job(s) due", len(due))
        ran = 0
        for job in due:
            # Re-load prompt fields; claimed row is authoritative.
            current = get_job(active, job.id) or job
            run_one_job(
                current,
                store=active,
                delivery_send=delivery_send,
                settings=settings,
                provider=provider,
            )
            ran += 1
        return ran
    finally:
        if owns_store:
            active.close()


def run_job_now(
    store: SessionStore,
    job_ref: str,
    *,
    delivery_send: DeliverySend | None = None,
) -> bool:
    """Claim one job for immediate execution and run it."""
    from agent.schedule.jobs import compute_next_run_at, resolve_job

    job = resolve_job(store, job_ref)
    if job.state == "completed":
        raise ValueError("Completed jobs cannot be run.")
    now_ts = time.time()
    next_run = compute_next_run_at(
        kind=job.schedule_kind,
        expr=job.schedule_expr,
        from_time=now_ts,
    )
    store.update_scheduled_job(
        job.id,
        {
            "enabled": 1,
            "state": "running",
            "next_run_at": next_run,
            "updated_at": now_ts,
        },
    )
    current = get_job(store, job.id)
    if current is None:
        return False
    return run_one_job(current, store=store, delivery_send=delivery_send)


__all__ = [
    "SILENT_MARKER",
    "deliver_result",
    "run_job",
    "run_job_now",
    "run_one_job",
    "schedule_output_dir",
    "schedule_toolsets_for_run",
    "tick",
]
