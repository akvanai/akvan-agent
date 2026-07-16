"""Logging setup and configuration tests."""

from __future__ import annotations

import logging
import stat
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from agent import logging_setup
from agent.logging_config import load_logging_config
from agent.logging_setup import (
    RedactingFormatter,
    clear_session_context,
    logs_dir,
    set_session_context,
    setup_logging,
    truncate_summary,
)
from agent.storage.permissions import DIR_MODE, FILE_MODE


@pytest.fixture(autouse=True)
def reset_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate logging state between tests."""
    monkeypatch.setattr(logging_setup, "_logging_initialized", False)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.setLevel(logging.WARNING)


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".akvan"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_setup_logging_creates_agent_and_errors_logs(akvan_home: Path) -> None:
    log_dir = setup_logging(project_root=akvan_home, mode="cli")
    assert log_dir == akvan_home / "logs"
    assert (log_dir / "agent.log").exists()
    assert (log_dir / "errors.log").exists()
    assert stat.S_IMODE(log_dir.stat().st_mode) == DIR_MODE
    assert stat.S_IMODE((log_dir / "agent.log").stat().st_mode) == FILE_MODE
    assert stat.S_IMODE((log_dir / "errors.log").stat().st_mode) == FILE_MODE


def test_setup_logging_gateway_mode(akvan_home: Path) -> None:
    log_dir = setup_logging(
        project_root=akvan_home, mode="gateway", gateway_id="telegram"
    )
    assert (log_dir / "gateway-telegram.log").exists()
    assert not (log_dir / "agent.log").exists()


def test_setup_logging_is_idempotent(akvan_home: Path) -> None:
    setup_logging(project_root=akvan_home, mode="cli")
    handler_count = len(logging.getLogger().handlers)
    setup_logging(project_root=akvan_home, mode="cli")
    assert len(logging.getLogger().handlers) == handler_count


def test_warning_appears_in_both_logs(akvan_home: Path) -> None:
    setup_logging(project_root=akvan_home, mode="cli")
    logging.getLogger("test.warning").warning("something went wrong")
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.flush()

    agent_log = (akvan_home / "logs" / "agent.log").read_text(encoding="utf-8")
    errors_log = (akvan_home / "logs" / "errors.log").read_text(encoding="utf-8")
    assert "something went wrong" in agent_log
    assert "something went wrong" in errors_log


def test_redacting_formatter_masks_api_key() -> None:
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="key is sk-abcdefghijklmnopqrstuvwxyz",
        args=(),
        exc_info=None,
    )
    assert "sk-***" in formatter.format(record)
    assert "abcdefghijklmnopqrstuvwxyz" not in formatter.format(record)


def test_session_context_tag(akvan_home: Path) -> None:
    setup_logging(project_root=akvan_home, mode="cli")
    set_session_context("session-abc-123")
    logging.getLogger("akvan.session").info("started")
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.flush()
    clear_session_context()

    agent_log = (akvan_home / "logs" / "agent.log").read_text(encoding="utf-8")
    assert "[session-]" in agent_log
    assert "started" in agent_log


def test_config_yaml_overrides_defaults(akvan_home: Path) -> None:
    (akvan_home / "config.yaml").write_text(
        "logging:\n  level: DEBUG\n  max_size_mb: 10\n  backup_count: 5\n",
        encoding="utf-8",
    )
    cfg = load_logging_config(project_root=akvan_home)
    assert cfg.level == "DEBUG"
    assert cfg.max_size_mb == 10
    assert cfg.backup_count == 5


def test_akvan_log_level_env_override(akvan_home: Path, monkeypatch) -> None:
    (akvan_home / "config.yaml").write_text(
        "logging:\n  level: INFO\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AKVAN_LOG_LEVEL", "ERROR")
    cfg = load_logging_config(project_root=akvan_home)
    assert cfg.level == "ERROR"


def test_truncate_summary() -> None:
    short = truncate_summary("hello world")
    assert short == "hello world"
    long_text = "x" * 200
    truncated = truncate_summary(long_text)
    assert len(truncated) <= 120
    assert truncated.endswith("…")


def test_logs_dir(akvan_home: Path) -> None:
    assert logs_dir(project_root=akvan_home) == akvan_home / "logs"
