"""CLI helpers for scheduled jobs."""

from __future__ import annotations

import argparse

from rich.console import Console

from agent.schedule.jobs import (
    create_job,
    format_job_line,
    list_jobs,
    pause_job,
    remove_job,
    resume_job,
)
from agent.schedule.scheduler import run_job_now, tick
from agent.storage.store import open_session_store


def run_schedule_cli(console: Console, args: argparse.Namespace) -> int:
    store = open_session_store()
    if store is None:
        console.print("[red]Session database not available.[/red]")
        return 1
    try:
        command = getattr(args, "schedule_command", None)
        if command == "list" or command is None:
            jobs = list_jobs(store, include_completed=True)
            if not jobs:
                console.print("No scheduled jobs.")
                return 0
            for job in jobs:
                console.print(format_job_line(job))
            return 0
        if command == "create":
            job = create_job(
                store,
                schedule=args.schedule,
                prompt=args.prompt,
                name=args.name,
                deliver=args.deliver,
            )
            console.print(f"Created job {job.id}")
            console.print(format_job_line(job))
            return 0
        if command == "pause":
            job = pause_job(store, args.job_ref)
            console.print(f"Paused {job.id} ({job.name})")
            return 0
        if command == "resume":
            job = resume_job(store, args.job_ref)
            console.print(f"Resumed {job.id} ({job.name})")
            return 0
        if command == "remove":
            job = remove_job(store, args.job_ref)
            console.print(f"Removed {job.id} ({job.name})")
            return 0
        if command == "run":
            ok = run_job_now(store, args.job_ref)
            console.print(
                f"Ran {args.job_ref}: {'ok' if ok else 'failed'}"
            )
            return 0 if ok else 1
        if command == "tick":
            count = tick(store)
            console.print(f"Executed {count} due job(s).")
            return 0
        console.print(f"Unknown schedule command: {command}")
        return 1
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    finally:
        store.close()
