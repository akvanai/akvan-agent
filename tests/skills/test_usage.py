"""Usage tracking tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills.usage import bump_use, get_record, is_bundled, load_usage


def test_bump_use_increments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))

    bump_use("plan")
    bump_use("plan")
    record = get_record("plan")
    assert record.get("use_count") == 2
    assert "last_used_at" in record


def test_is_bundled_reads_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    skills = home / "skills"
    skills.mkdir(parents=True)
    (skills / ".bundled_manifest").write_text("plan:abc123\n", encoding="utf-8")
    monkeypatch.setenv("AKVAN_HOME", str(home))
    assert is_bundled("plan")
    assert not is_bundled("custom-skill")
