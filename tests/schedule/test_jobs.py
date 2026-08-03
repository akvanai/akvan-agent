"""Tests for scheduled job parsing and store CRUD."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.schedule.jobs import (
    ScheduleOrigin,
    claim_due_jobs,
    create_job,
    finish_job_run,
    get_job,
    list_jobs,
    parse_schedule,
    pause_job,
    remove_job,
    resume_job,
)
from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    home = tmp_path / ".akvan"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AKVAN_HOME", str(home))
    db = SessionStore(db_path=home / "state.db")
    yield db
    db.close()


def test_schema_v6_creates_scheduled_jobs(store: SessionStore) -> None:
    row = store._conn.execute(
        "SELECT version FROM schema_version LIMIT 1"
    ).fetchone()
    assert int(row["version"]) >= 6
    tables = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "scheduled_jobs" in tables


def test_parse_schedule_kinds() -> None:
    once = parse_schedule("30m", now=1_700_000_000.0)
    assert once.kind == "once"
    assert once.next_run_at == 1_700_000_000.0 + 30 * 60

    interval = parse_schedule("every 2h", now=1_700_000_000.0)
    assert interval.kind == "interval"
    assert interval.minutes == 120
    assert interval.next_run_at == 1_700_000_000.0 + 120 * 60

    cron = parse_schedule("0 9 * * *", now=1_700_000_000.0)
    assert cron.kind == "cron"
    assert cron.expr == "0 9 * * *"
    assert cron.next_run_at > 1_700_000_000.0

    iso = parse_schedule("2030-01-15T09:00:00+00:00")
    assert iso.kind == "once"
    assert "2030-01-15" in iso.display


def test_create_list_pause_resume_remove(store: SessionStore) -> None:
    job = create_job(
        store,
        schedule="every 1h",
        prompt="Check status",
        name="status-check",
        origin=ScheduleOrigin(platform="telegram", chat_id="42"),
    )
    assert job.deliver == "origin"
    assert job.origin_chat_id == "42"
    assert get_job(store, "status-check") is not None

    paused = pause_job(store, job.id)
    assert paused.state == "paused"
    assert paused.enabled is False

    resumed = resume_job(store, job.id)
    assert resumed.state == "scheduled"
    assert resumed.enabled is True

    removed = remove_job(store, job.id)
    assert removed.id == job.id
    assert get_job(store, job.id) is None
    assert list_jobs(store) == []


def test_claim_due_and_finish_one_shot(store: SessionStore) -> None:
    now = time.time()
    job = create_job(
        store,
        schedule="1m",
        prompt="Remind me",
        name="reminder",
        now=now - 120,
    )
    # Force due
    store.update_scheduled_job(job.id, {"next_run_at": now - 1})
    claimed = claim_due_jobs(store, now=now)
    assert len(claimed) == 1
    assert claimed[0].state == "running"
    assert claimed[0].next_run_at is None  # one-shot advanced to None

    finish_job_run(store, claimed[0], success=True, now=now)
    done = get_job(store, job.id)
    assert done is not None
    assert done.state == "completed"
    assert done.last_status == "ok"


def test_claim_interval_advances_next_run(store: SessionStore) -> None:
    now = time.time()
    job = create_job(
        store,
        schedule="every 30m",
        prompt="Poll",
        name="poll",
        now=now - 3600,
    )
    store.update_scheduled_job(job.id, {"next_run_at": now - 1})
    claimed = claim_due_jobs(store, now=now)
    assert len(claimed) == 1
    assert claimed[0].next_run_at is not None
    assert claimed[0].next_run_at >= now + 30 * 60 - 1

    finish_job_run(store, claimed[0], success=True, now=now)
    active = get_job(store, job.id)
    assert active is not None
    assert active.state == "scheduled"
