"""Layered prompt construction tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.prompts import PromptBuilder
from agent.vault import vault_dir


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


def test_layered_prompt_order_and_project_instruction_precedence(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    nested = project / "src"
    home = tmp_path / "home"
    nested.mkdir(parents=True)
    home.mkdir()
    (project / ".git").mkdir()
    akvan = home / ".akvan"
    akvan.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(akvan))
    (akvan / "SOUL.md").write_text("CUSTOM IDENTITY", encoding="utf-8")
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
    assert f"- Agent vault: {vault_dir()}" in snapshot.content
    assert "agent vault path from" in snapshot.content
    assert (akvan / "vault").is_dir()
    assert snapshot.content == builder.build(
        model="model", provider="fake", skills=skills, tools=()
    ).content


def test_browser_guidance_includes_upload_when_browser_tools_present(
    tmp_path: Path, monkeypatch
) -> None:
    from agent.tools.base import Tool

    home = tmp_path / "home"
    home.mkdir()
    akvan = home / ".akvan"
    akvan.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(akvan))
    (akvan / "SOUL.md").write_text("IDENTITY", encoding="utf-8")

    fake_browser = Tool(
        name="browser_start",
        description="start",
        parameters={"type": "object", "properties": {}},
        run=lambda: "ok",
    )
    builder = PromptBuilder(cwd=tmp_path, user_home=home)
    skills = builder.discover_skills()
    snapshot = builder.build(
        model="model", provider="fake", skills=skills, tools=(fake_browser,)
    )
    assert "browser_upload" in snapshot.content
    assert "Browser Tools" in snapshot.content
