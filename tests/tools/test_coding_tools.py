from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent.skills import SkillRegistry
from agent.tools.file_tools import build_file_tools
from agent.tools.process_manager import ProcessManager
from agent.tools.registry import DEFAULT_TOOLSETS, build_registry
from agent.tools.terminal_tools import build_terminal_tools


def by_name(tools):
    return {tool.name: tool for tool in tools}


def test_registry_resolves_coding_toolsets(tmp_path: Path) -> None:
    skills = SkillRegistry.discover(
        user_root=tmp_path / "home", project_root=tmp_path
    )
    manager = ProcessManager()
    registry = build_registry(
        skills, project_root=tmp_path, process_manager=manager
    )

    tools = by_name(registry.resolve(DEFAULT_TOOLSETS))
    assert {
        "read_file",
        "write_file",
        "patch",
        "terminal",
        "process",
        "skills_list",
        "skill_view",
        "skill_manage",
        "vision_analyze",
    } == set(tools)
    manager.cleanup()


def test_file_tools_write_patch_and_classify_boundaries(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    tools = by_name(build_file_tools(project))
    target = project / "sample.py"

    assert tools["write_file"].approval({"path": "sample.py"}) is None
    tools["write_file"].invoke(
        {"path": "sample.py", "content": "value = 1\n"}
    )
    result = tools["patch"].invoke(
        {
            "path": "sample.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        }
    )

    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert "-value = 1" in result.content
    assert tools["write_file"].approval(
        {"path": str(tmp_path / "outside.txt")}
    ) is not None
    assert tools["write_file"].approval({"path": ".env.local"}) is not None


def test_file_tools_reject_symlink_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "link").symlink_to(outside, target_is_directory=True)
    tool = by_name(build_file_tools(project))["write_file"]

    with pytest.raises(ValueError, match="symlink"):
        tool.invoke({"path": "link/escaped.txt", "content": "no"})


def test_read_file_blocks_sensitive_env(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("SECRET=1\n", encoding="utf-8")
    read_tool = by_name(build_file_tools(project))["read_file"]

    with pytest.raises(ValueError, match="Access denied"):
        read_tool.invoke({"path": ".env"})


def test_read_file_allows_outside_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "elsewhere" / "notes.txt"
    outside.parent.mkdir()
    outside.write_text("outside content\n", encoding="utf-8")
    read_tool = by_name(build_file_tools(project))["read_file"]

    result = read_tool.invoke({"path": str(outside)})

    assert result.content == "outside content\n"


def test_terminal_foreground_and_background_processes(tmp_path: Path) -> None:
    manager = ProcessManager()
    tools = by_name(build_terminal_tools(tmp_path, manager))

    foreground = json.loads(
        tools["terminal"].invoke({"command": "printf hello"}).content
    )
    assert foreground["exit_code"] == 0
    assert foreground["output"] == "hello"

    started = json.loads(
        tools["terminal"].invoke(
            {"command": "printf background", "background": True}
        ).content
    )
    session_id = started["session_id"]
    time.sleep(0.05)
    waited = json.loads(
        tools["process"].invoke(
            {"action": "wait", "session_id": session_id, "timeout": 1}
        ).content
    )
    assert not waited["running"]
    assert "background" in waited["output"]
    tools["process"].invoke({"action": "close", "session_id": session_id})

    with pytest.raises(ValueError, match="Unknown process"):
        tools["process"].invoke(
            {"action": "kill", "session_id": "not-owned"}
        )
    manager.cleanup()
