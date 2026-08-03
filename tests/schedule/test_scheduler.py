"""Tests for schedule runner helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.schedule.jobs import create_job
from agent.schedule.scheduler import (
    SILENT_MARKER,
    deliver_result,
    run_job,
    schedule_toolsets_for_run,
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


def test_schedule_toolsets_exclude_schedule() -> None:
    toolsets = schedule_toolsets_for_run()
    assert "schedule" not in toolsets


def test_silent_delivery_skips_send(store: SessionStore) -> None:
    job = create_job(store, schedule="1h", prompt="x", name="silent")
    sent: list[tuple[str, str]] = []

    err = deliver_result(
        job,
        f"{SILENT_MARKER}\nnothing to say",
        delivery_send=lambda cid, text: sent.append((cid, text)),
        wrap=False,
    )
    assert err is None
    assert sent == []


def test_run_job_uses_fresh_session(store: SessionStore, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / ".akvan"))
    job = create_job(store, schedule="1h", prompt="Say hi", name="hi", deliver="local")

    fake_session = MagicMock()
    fake_session.messages = []
    fake_session.loop.run_turn.return_value = "hello from schedule"

    fake_settings = MagicMock()
    fake_settings.model = "test-model"
    fake_settings.max_iterations = 5
    fake_settings.approval_mode = "off"
    fake_settings.approval_timeout = 60
    fake_settings.terminal_timeout = 120

    fake_provider = MagicMock()

    with (
        patch("agent.config.load_settings", return_value=fake_settings),
        patch("agent.providers.build_provider", return_value=fake_provider),
        patch("agent.session.AgentSession.create", return_value=fake_session) as create,
        patch("agent.schedule.scheduler.schedule_toolsets_for_run", return_value=("core",)),
    ):
        ok, answer, error = run_job(job, store=store, provider=fake_provider, settings=fake_settings)

    assert ok is True
    assert answer == "hello from schedule"
    assert error is None
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["yolo"] is True
    assert kwargs["session_source"] == "schedule"
    assert kwargs["enabled_toolsets"] == ("core",)
    assert "schedule" not in kwargs["enabled_toolsets"]
    fake_session.end.assert_called_once()
    fake_provider.close.assert_not_called()  # owned by caller
