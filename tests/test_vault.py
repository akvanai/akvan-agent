"""Tests for the agent media vault."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent.storage.permissions import DIR_MODE, harden_akvan_home
from agent.vault import ensure_vault, is_under_vault, vault_dir


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".akvan"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_vault_dir_defaults_under_akvan_home(akvan_home: Path) -> None:
    assert vault_dir() == akvan_home / "vault"
    assert is_under_vault(akvan_home / "vault" / "banner.png")
    assert not is_under_vault(akvan_home / ".env")
    assert not is_under_vault(akvan_home / "browser" / "profiles" / "x")


def test_vault_dir_respects_config_override(
    akvan_home: Path, tmp_path: Path
) -> None:
    custom = tmp_path / "media-vault"
    akvan_home.mkdir(parents=True)
    (akvan_home / "config.yaml").write_text(
        f"vault:\n  root_dir: {custom}\n",
        encoding="utf-8",
    )
    assert vault_dir() == custom
    assert is_under_vault(custom / "download.bin")
    assert not is_under_vault(akvan_home / "vault" / "x.png")


def test_ensure_vault_and_harden(akvan_home: Path) -> None:
    path = ensure_vault()
    assert path == akvan_home / "vault"
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == DIR_MODE

    harden_akvan_home(akvan_home)
    assert (akvan_home / "vault").is_dir()
    assert stat.S_IMODE((akvan_home / "vault").stat().st_mode) == DIR_MODE
