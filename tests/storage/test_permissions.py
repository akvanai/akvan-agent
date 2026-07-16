"""Tests for Akvan home and session database permissions."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agent.storage.permissions import (
    DIR_MODE,
    FILE_MODE,
    harden_akvan_home,
)
from agent.storage.store import SessionStore, open_session_store


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".akvan"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_open_session_store_secures_home_and_db(akvan_home: Path) -> None:
    store = open_session_store()
    assert store is not None

    assert _mode(akvan_home) == DIR_MODE
    assert _mode(akvan_home / "state.db") == FILE_MODE

    store.close()


def test_harden_migrates_loose_permissions(akvan_home: Path) -> None:
    akvan_home.mkdir(mode=0o775)
    db_path = akvan_home / "state.db"
    db_path.write_text("legacy", encoding="utf-8")
    os.chmod(db_path, 0o644)
    os.chmod(akvan_home, 0o775)

    harden_akvan_home(akvan_home)

    assert _mode(akvan_home) == DIR_MODE
    assert _mode(db_path) == FILE_MODE


def test_custom_db_path_skips_hardening(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    os.chmod(tmp_path, 0o755)

    store = SessionStore(db_path=db_path)
    store.create_session("sess-1", source="cli")
    store.close()

    assert _mode(tmp_path) == 0o755


def test_wal_shm_secured(akvan_home: Path) -> None:
    store = open_session_store()
    assert store is not None
    store.create_session("sess-wal", source="cli")
    store.append_message("sess-wal", {"role": "user", "content": "hello"})
    store.close()

    db_path = akvan_home / "state.db"
    assert _mode(db_path) == FILE_MODE

    wal_path = akvan_home / "state.db-wal"
    shm_path = akvan_home / "state.db-shm"
    if wal_path.exists():
        assert _mode(wal_path) == FILE_MODE
    if shm_path.exists():
        assert _mode(shm_path) == FILE_MODE
