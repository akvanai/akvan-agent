"""Tests for skill_manage."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills.manage import skill_manage
from agent.skills.provenance import (
    BACKGROUND_REVIEW,
    reset_current_write_origin,
    set_current_write_origin,
)
from agent.skills.usage import is_agent_created, load_usage


def _skill_md(name: str, description: str, body: str = "Steps here.") -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"# {name.title()}\n\n{body}\n"
    )


def test_create_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))

    result = skill_manage(
        action="create",
        name="my-workflow",
        category="software-development",
        content=_skill_md("my-workflow", "Run the deploy workflow."),
    )
    assert result["success"] is True
    path = home / "skills" / "software-development" / "my-workflow" / "SKILL.md"
    assert path.is_file()


def test_background_create_marks_agent_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    token = set_current_write_origin(BACKGROUND_REVIEW)
    try:
        result = skill_manage(
            action="create",
            name="auto-skill",
            category="general",
            content=_skill_md("auto-skill", "Auto captured workflow."),
        )
    finally:
        reset_current_write_origin(token)
    assert result["success"] is True
    assert is_agent_created("auto-skill")


def test_foreground_create_not_agent_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    result = skill_manage(
        action="create",
        name="user-skill",
        category="general",
        content=_skill_md("user-skill", "User directed workflow."),
    )
    assert result["success"] is True
    assert not is_agent_created("user-skill")


def test_patch_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    skill_manage(
        action="create",
        name="patch-me",
        category="general",
        content=_skill_md("patch-me", "Original description.", "OLD"),
    )
    result = skill_manage(
        action="patch",
        name="patch-me",
        old_string="OLD",
        new_string="NEW",
    )
    assert result["success"] is True
    text = (home / "skills" / "general" / "patch-me" / "SKILL.md").read_text()
    assert "NEW" in text
