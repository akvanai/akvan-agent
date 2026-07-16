"""Skill discovery, precedence, resource, and safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills import SkillError, SkillRegistry
from agent.skills.tools import build_skill_tools


def write_skill(
    root: Path,
    category: str,
    name: str,
    description: str,
    body: str,
) -> Path:
    skill_root = root / ".akvan" / "skills" / category / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_root


def test_skill_precedence_snapshot_resources_and_safety(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write_skill(home, "writing", "writer", "User writer", "USER BODY")
    project_skill = write_skill(
        project, "writing", "writer", "Project writer", "PROJECT BODY"
    )
    resource = project_skill / "references" / "guide.md"
    resource.parent.mkdir()
    resource.write_text("OLD RESOURCE", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    (project_skill / "escape.txt").symlink_to(outside)
    (project_skill / "large.txt").write_text(
        "x" * (64 * 1024 + 1), encoding="utf-8"
    )

    registry = SkillRegistry.discover(user_root=home, project_root=project)
    resource.write_text("NEW RESOURCE", encoding="utf-8")

    skill = registry.require("writer")
    assert skill.origin == "project"
    assert skill.category == "writing"
    assert "PROJECT BODY" in registry.view("writer")
    tools = build_skill_tools(registry)
    assert [tool.name for tool in tools] == ["skills_list", "skill_view"]
    catalog = tools[0].invoke({})
    assert "writer" in catalog.content
    assert "writing" in catalog.content
    loaded = tools[1].invoke({"name": "writer"})
    assert "trusted_local_instructions" in loaded.render(source="skill_view")
    assert "NEW RESOURCE" in registry.view("writer", "references/guide.md")
    with pytest.raises(SkillError, match="inside"):
        registry.view("writer", "../secret.txt")
    with pytest.raises(SkillError, match="Symlinked"):
        registry.view("writer", "escape.txt")
    large = registry.view("writer", "large.txt")
    assert "x" * (64 * 1024 + 1) in large
    duplicate = tools[1].invoke({"name": "writer"})
    assert "already loaded" in duplicate.content


def test_large_skill_warns_but_loads_fully(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    body = "instruction\n" * 6_000
    write_skill(project, "design", "hallmark", "Large design workflow", body)

    registry = SkillRegistry.discover(user_root=home, project_root=project)

    assert any("large SKILL.md" in warning for warning in registry.warnings)
    loaded = registry.view("hallmark")
    assert "\n".join(["instruction"] * 6_000) in loaded


def test_skill_resource_over_hard_limit_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    skill_root = write_skill(project, "design", "huge", "Huge resource", "body")
    resource = skill_root / "references" / "huge.md"
    resource.parent.mkdir()
    resource.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    registry = SkillRegistry.discover(user_root=home, project_root=project)

    with pytest.raises(SkillError, match="safety limit"):
        registry.view("huge", "references/huge.md")


def test_flat_skill_paths_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    flat = home / ".akvan" / "skills" / "writer"
    flat.mkdir(parents=True)
    (flat / "SKILL.md").write_text(
        "---\nname: writer\ndescription: flat\n---\n\nbody\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.discover(user_root=home, project_root=tmp_path / "project")

    assert "writer" not in registry.skills
    assert any("skills/<category>/<name>/SKILL.md" in warning for warning in registry.warnings)
