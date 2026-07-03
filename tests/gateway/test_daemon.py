"""Gateway daemon and state tests."""

from __future__ import annotations

import subprocess
import sys

from agent.gateway.daemon import (
    _daemon_command,
    clear_gateway_pid,
    is_gateway_running,
    read_gateway_pid,
    start_gateway_daemon,
    stop_gateway_daemon,
    write_gateway_pid,
)
from agent.gateway.state import is_gateway_enabled, set_gateway_enabled


def test_gateway_enabled_state_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    assert not is_gateway_enabled("telegram")
    set_gateway_enabled("telegram", True)
    assert is_gateway_enabled("telegram")
    set_gateway_enabled("telegram", False)
    assert not is_gateway_enabled("telegram")


def test_gateway_pid_lifecycle(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    assert read_gateway_pid("telegram") is None
    write_gateway_pid("telegram", 4242)
    assert read_gateway_pid("telegram") == 4242
    clear_gateway_pid("telegram")
    assert read_gateway_pid("telegram") is None


def test_is_gateway_running_clears_stale_pid(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    write_gateway_pid("telegram", 999999)
    assert not is_gateway_running("telegram")


def test_start_and_stop_gateway_daemon(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    real_popen = subprocess.Popen

    def fake_popen(command, **kwargs):
        return real_popen(
            ["sleep", "30"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    monkeypatch.setattr("agent.gateway.daemon.subprocess.Popen", fake_popen)

    started, message = start_gateway_daemon("telegram")
    assert started is True
    assert "started in the background" in message.lower()
    assert is_gateway_running("telegram")

    stopped, stop_message = stop_gateway_daemon("telegram")
    assert stopped is True
    assert "stopped" in stop_message.lower()
    assert not is_gateway_running("telegram")


def test_daemon_command_carries_gateway_identity() -> None:
    telegram = _daemon_command("telegram", yolo=False, max_iterations=30)
    slack = _daemon_command("slack", yolo=True, max_iterations=12)
    assert telegram[telegram.index("--gateway-id") + 1] == "telegram"
    assert slack[slack.index("--gateway-id") + 1] == "slack"
    assert "--yolo" not in telegram
    assert "--yolo" in slack
