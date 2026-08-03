"""Tests for the schedule agent tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.schedule.jobs import ScheduleOrigin, get_job
from agent.storage.store import SessionStore
from agent.tools.schedule_tools import build_schedule_tools, schedule_tool


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    home = tmp_path / ".akvan"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AKVAN_HOME", str(home))
    db = SessionStore(db_path=home / "state.db")
    yield db
    db.close()


def test_schedule_tool_create_stamps_origin(store: SessionStore) -> None:
    origin = ScheduleOrigin(platform="telegram", chat_id="99")
    raw = schedule_tool(
        action="create",
        schedule="every 1h",
        prompt="Digest news",
        name="digest",
        store=store,
        origin=origin,
    )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["job"]["origin_chat_id"] == "99"
    assert payload["job"]["deliver"] == "origin"

    listed = json.loads(schedule_tool(action="list", store=store))
    assert listed["success"] is True
    assert len(listed["jobs"]) == 1


def test_build_schedule_tools_register(store: SessionStore) -> None:
    tools = build_schedule_tools(
        store,
        origin=ScheduleOrigin(platform="telegram", chat_id="1"),
    )
    assert len(tools) == 1
    assert tools[0].name == "schedule"
    result = tools[0].invoke(
        {
            "action": "create",
            "schedule": "30m",
            "prompt": "Ping me",
            "name": "ping",
        }
    )
    payload = json.loads(result.content)
    assert payload["success"] is True
    job = get_job(store, "ping")
    assert job is not None
    assert job.origin_chat_id == "1"
