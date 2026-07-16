"""Curator tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.skills.curator import archive_stale, curator_status
from agent.skills.manage import skill_manage
from agent.skills.provenance import BACKGROUND_REVIEW, reset_current_write_origin, set_current_write_origin
from agent.skills.usage import load_usage


def _skill_md(name: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Short desc.\n---\n\n# {name}\n\nBody.\n"
    )


def test_archive_stale_agent_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    token = set_current_write_origin(BACKGROUND_REVIEW)
    try:
        skill_manage(
            action="create",
            name="stale-skill",
            category="general",
            content=_skill_md("stale-skill"),
        )
    finally:
        reset_current_write_origin(token)

    usage = load_usage()
    usage["stale-skill"]["last_activity_at"] = time.time() - 100 * 86400
    from agent.skills import usage as usage_mod

    usage_mod._write_usage(usage)

    result = archive_stale(days=90)
    assert "stale-skill" in result["archived"]
    status = curator_status()
    assert status["archived"] >= 1
