"""Layered prompt construction tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.prompts import PromptBuilder


def write_skill(
    root: Path, category: str, name: str, description: str, body: str
) -> Path:
    skill_root = root / ".akvan" / "skills" / category / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_root


def test_layered_prompt_order_and_project_instruction_precedence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "src"
    home = tmp_path / "home"
    nested.mkdir(parents=True)
    home.mkdir()
    (project / ".git").mkdir()
    (home / ".akvan").mkdir()
    (home / ".akvan" / "SOUL.md").write_text("CUSTOM IDENTITY", encoding="utf-8")
    (project / "AGENTS.md").write_text("AGENTS RULE", encoding="utf-8")
    (project / ".akvan.md").write_text("AKVAN RULE", encoding="utf-8")
    write_skill(project, "writing", "writer", "Write clearly", "SECRET FULL BODY")

    builder = PromptBuilder(
        cwd=nested,
        user_home=home,
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    skills = builder.discover_skills()
    snapshot = builder.build(
        model="model", provider="fake", skills=skills, tools=()
    )

    assert snapshot.content.index("CUSTOM IDENTITY") < snapshot.content.index("Runtime Guidance")
    assert snapshot.content.index("Runtime Guidance") < snapshot.content.index("Available Skills")
    assert snapshot.content.index("Available Skills") < snapshot.content.index("AKVAN RULE")
    assert snapshot.content.index("AKVAN RULE") < snapshot.content.index("Session Metadata")
    assert "AGENTS RULE" not in snapshot.content
    assert "SECRET FULL BODY" not in snapshot.content
    assert "writer (writing): Write clearly" in snapshot.content
    assert snapshot.content == builder.build(
        model="model", provider="fake", skills=skills, tools=()
    ).content

