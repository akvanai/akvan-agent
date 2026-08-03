"""Scheduled task execution for Akvan Agent."""

from agent.schedule.config import ScheduleConfig, is_schedule_enabled, load_schedule_config
from agent.schedule.jobs import (
    ScheduleOrigin,
    ScheduledJob,
    create_job,
    list_jobs,
    parse_schedule,
    pause_job,
    remove_job,
    resume_job,
    trigger_job,
    update_job,
)

__all__ = [
    "ScheduleConfig",
    "ScheduleOrigin",
    "ScheduledJob",
    "create_job",
    "is_schedule_enabled",
    "list_jobs",
    "load_schedule_config",
    "parse_schedule",
    "pause_job",
    "remove_job",
    "resume_job",
    "trigger_job",
    "update_job",
]


def tick(*args, **kwargs):
    """Lazy re-export to avoid circular imports at package import time."""
    from agent.schedule.scheduler import tick as _tick

    return _tick(*args, **kwargs)
