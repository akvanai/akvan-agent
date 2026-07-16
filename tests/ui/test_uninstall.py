"""Tests for akvan uninstall command."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.ui.app import build_parser
from agent.ui.uninstall import launcher_path, remove_launcher, run_uninstall


def test_build_parser_accepts_uninstall_subcommand() -> None:
    args = build_parser().parse_args(["uninstall"])
    assert args.command == "uninstall"
    assert args.purge is False
    assert args.yes is False

    purge_args = build_parser().parse_args(["uninstall", "--purge", "--yes"])
    assert purge_args.purge is True
    assert purge_args.yes is True


def test_run_uninstall_preserves_user_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "akvan-home"
    venv_dir = home / "venv"
    app_dir = home / "app"
    data_file = home / "SOUL.md"
    venv_dir.mkdir(parents=True)
    app_dir.mkdir()
    data_file.write_text("identity", encoding="utf-8")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setattr("agent.ui.uninstall.remove_managed_containers", lambda: None)
    monkeypatch.setattr("agent.ui.uninstall.remove_launcher", lambda **kwargs: False)

    assert run_uninstall(purge=False, yes=True) == 0
    assert not venv_dir.exists()
    assert not app_dir.exists()
    assert data_file.exists()


def test_run_uninstall_purge_removes_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "akvan-home"
    home.mkdir()
    (home / "state.db").write_text("data", encoding="utf-8")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setattr("agent.ui.uninstall.remove_managed_containers", lambda: None)
    monkeypatch.setattr("agent.ui.uninstall.remove_launcher", lambda **kwargs: False)

    assert run_uninstall(purge=True, yes=True) == 0
    assert not home.exists()


def test_run_uninstall_requires_yes_when_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "akvan-home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert run_uninstall(purge=False, yes=False) == 2


def test_remove_launcher_only_when_pointing_at_venv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "akvan-home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    venv_bin = home / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    venv_akvan = venv_bin / "akvan"
    venv_akvan.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher = bin_dir / "akvan"
    launcher.symlink_to(venv_akvan)

    monkeypatch.setenv("AKVAN_BIN_DIR", str(bin_dir))
    assert remove_launcher(home=home) is True
    assert not launcher.exists()


def test_launcher_path_honors_akvan_bin_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_BIN_DIR", str(tmp_path / "custom-bin"))
    assert launcher_path() == tmp_path / "custom-bin" / "akvan"
