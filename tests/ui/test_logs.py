"""Tests for ``akvan logs`` CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.ui.app import build_parser
from agent.ui.logs import _read_tail, resolve_log_path


def test_build_parser_accepts_logs_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["logs", "-f", "--lines", "10"])
    assert args.command == "logs"
    assert args.follow is True
    assert args.lines == 10


def test_resolve_log_path_gateway() -> None:
    path = resolve_log_path("gateway", "telegram")
    assert path is not None
    assert path.name == "gateway-telegram.log"
    assert path.parent.name == "logs"


def test_read_tail_returns_last_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    lines = _read_tail(log_file, 2)
    assert lines == ["line2\n", "line3\n"]


def test_read_tail_with_level_filter(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text(
        "2026-07-05 10:00:00 INFO akvan.memory: ok\n"
        "2026-07-05 10:00:01 DEBUG akvan.memory: detail\n",
        encoding="utf-8",
    )
    lines = _read_tail(
        log_file,
        10,
        has_filters=True,
        min_level="INFO",
    )
    assert len(lines) == 1
    assert "ok" in lines[0]
