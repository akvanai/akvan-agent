"""Central registration and toolset resolution."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from agent.skills import SkillRegistry
from agent.skills.tools import build_skill_tools
from agent.tools.base import Tool
from agent.tools.file_tools import build_file_tools
from agent.tools.process_manager import ProcessManager
from agent.tools.terminal_tools import build_terminal_tools
from agent.tools.web.config import is_web_configured
from agent.tools.web.tools import build_web_tools


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._toolsets: dict[str, list[str]] = {}

    def register(self, tool: Tool, *, toolset: str) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered.")
        self._tools[tool.name] = tool
        self._toolsets.setdefault(toolset, []).append(tool.name)

    def register_many(
        self, tools: Iterable[Tool], *, toolset: str
    ) -> None:
        self._toolsets.setdefault(toolset, [])
        for tool in tools:
            self.register(tool, toolset=toolset)

    def resolve(self, toolsets: Iterable[str]) -> tuple[Tool, ...]:
        resolved: list[Tool] = []
        seen: set[str] = set()
        for toolset in toolsets:
            if toolset not in self._toolsets:
                raise ValueError(f"Unknown toolset {toolset!r}.")
            for name in self._toolsets[toolset]:
                if name not in seen:
                    seen.add(name)
                    resolved.append(self._tools[name])
        return tuple(resolved)

    @property
    def toolsets(self) -> tuple[str, ...]:
        return tuple(self._toolsets)


DEFAULT_TOOLSETS = ("core", "files", "terminal", "skills")


def default_enabled_toolsets(*, project_root: Path | None = None) -> tuple[str, ...]:
    from agent.config import resolve_enabled_toolsets

    return resolve_enabled_toolsets(DEFAULT_TOOLSETS, project_root=project_root)


BASE_TOOLS: tuple[Tool, ...] = ()
AVAILABLE_TOOLS: tuple[Tool, ...] = BASE_TOOLS


def build_registry(
    skills: SkillRegistry,
    *,
    project_root: Path,
    process_manager: ProcessManager,
    terminal_timeout: int = 120,
    base_tools: tuple[Tool, ...] = BASE_TOOLS,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(base_tools, toolset="core")
    registry.register_many(build_file_tools(project_root), toolset="files")
    registry.register_many(
        build_terminal_tools(
            project_root,
            process_manager,
            default_timeout=terminal_timeout,
        ),
        toolset="terminal",
    )
    registry.register_many(build_skill_tools(skills), toolset="skills")
    if is_web_configured(project_root=project_root):
        registry.register_many(build_web_tools(), toolset="web")
    return registry


def build_available_tools(
    skills: SkillRegistry,
    *,
    project_root: Path | None = None,
    process_manager: ProcessManager | None = None,
    terminal_timeout: int = 120,
    enabled_toolsets: tuple[str, ...] | None = None,
) -> tuple[Tool, ...]:
    root = (project_root or Path.cwd()).resolve()
    manager = process_manager or ProcessManager()
    resolved = (
        enabled_toolsets
        if enabled_toolsets is not None
        else default_enabled_toolsets(project_root=root)
    )
    return build_registry(
        skills,
        project_root=root,
        process_manager=manager,
        terminal_timeout=terminal_timeout,
    ).resolve(resolved)
