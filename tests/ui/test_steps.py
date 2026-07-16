"""Tests for the live turn step timeline."""

from __future__ import annotations

from agent.events import AgentEvent, AgentState
from agent.tools.base import Tool
from agent.tools.presentation import ToolPresentation
from agent.ui.steps import StepKind, TurnStepLog, render_live_turn

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


def test_turn_step_log_narration_tool_answer_sequence() -> None:
    log = TurnStepLog.from_tools((READ_FILE,))

    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Let me check the config first.\n"))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "config.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Found the setting."))
    log.feed(AgentEvent(AgentState.COMPLETED))

    rendered = render_live_turn(log, 80)

    assert "Reading file" in rendered
    assert "config.py" in rendered
    assert "Found the setting." in rendered
    assert log.answer_content() == "Found the setting."


def test_turn_step_log_answer_without_tools() -> None:
    log = TurnStepLog.from_tools(())

    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Hello there."))
    log.feed(AgentEvent(AgentState.COMPLETED))

    assert log.answer_content() == "Hello there."
    assert any(step.kind == StepKind.ANSWER for step in log.steps)
    assert not any(step.kind == StepKind.NARRATION for step in log.steps)


def test_turn_step_log_renders_in_progress_answer_before_completed() -> None:
    log = TurnStepLog.from_tools(())

    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Hello"))

    rendered = render_live_turn(log, 80)

    assert "Hello" in rendered
    assert log.answer_content() == ""


def test_turn_step_log_multiple_tools() -> None:
    log = TurnStepLog.from_tools((READ_FILE,))

    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "a.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "b.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="done"))
    log.feed(AgentEvent(AgentState.COMPLETED))

    tool_steps = [step for step in log.steps if step.kind == StepKind.TOOL]
    assert len(tool_steps) == 2
    rendered = render_live_turn(log, 80)
    assert "a.py" in rendered
    assert "b.py" in rendered


def test_turn_step_log_keeps_narration_between_tools_in_timeline() -> None:
    log = TurnStepLog.from_tools((READ_FILE,))

    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="First file."))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "a.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="Second file."))
    log.feed(
        AgentEvent(
            AgentState.RUNNING_TOOL,
            tool_name="read_file",
            tool_arguments={"path": "b.py"},
        )
    )
    log.feed(AgentEvent(AgentState.THINKING))
    log.feed(AgentEvent(AgentState.RESPONDING, content="All done."))
    log.feed(AgentEvent(AgentState.COMPLETED))

    narration = [step for step in log.steps if step.kind == StepKind.NARRATION]
    assert len(narration) == 2
    assert narration[0].text == "First file."
    assert narration[1].text == "Second file."
    assert log.answer_content() == "All done."

    rendered = render_live_turn(log, 80)
    assert "┊" in rendered
    assert "╭" in rendered
    assert "a.py" in rendered
    assert "b.py" in rendered
    assert "All done." in rendered
    assert rendered.index("b.py") < rendered.index("All done.")
