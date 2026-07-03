"""Tests for persisted turn rendering with timeline prefix and answer box."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from agent.events import AgentEvent, AgentState
from agent.tools.base import Tool
from agent.tools.presentation import ToolPresentation
from agent.ui.steps import TurnStepLog, render_persisted_turn

READ_FILE = Tool(
    name="read_file",
    description="Read a file.",
    parameters={"type": "object", "properties": {}},
    run=lambda path: path,
    presentation=ToolPresentation(
        emoji="📖",
        label="Reading file",
        format_detail=lambda args: str(args.get("path", "")),
    ),
)


def test_render_persisted_turn_shows_prefix_and_answer_box() -> None:
    log = TurnStepLog.from_tools((READ_FILE,))
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Checking config."))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "config.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Timeout is 60 seconds."))
    log.feed(AgentEvent(AgentState.COMPLETED))

    output = StringIO()
    console = Console(file=output, force_terminal=True, width=80)
    render_persisted_turn(console, log)
    rendered = output.getvalue()

    assert "┊" in rendered
    assert "config.py" in rendered
    assert "Timeout is 60 seconds." in rendered
    assert "╭" in rendered
    assert "╰" in rendered
    assert rendered.index("config.py") < rendered.index("Timeout is 60 seconds.")


def test_render_persisted_turn_answer_only_has_box_without_prefix_rows() -> None:
    log = TurnStepLog.from_tools(())
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Hello there."))
    log.feed(AgentEvent(AgentState.COMPLETED))

    output = StringIO()
    console = Console(file=output, force_terminal=True, width=80)
    render_persisted_turn(console, log)
    rendered = output.getvalue()

    assert "Hello there." in rendered
    assert "╭" in rendered
    assert "┊" not in rendered
