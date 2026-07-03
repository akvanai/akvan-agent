"""Live turn timeline accumulation and rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from io import StringIO

from rich.console import Console
from rich.text import Text

from agent.events import AgentEvent, AgentState
from agent.tools.approval import ApprovalRequest
from agent.tools.base import Tool
from agent.tools.presentation import format_tool_line
from agent.ui.rendering import Spinner, render_answer_box

TIMELINE_PREFIX = "┊ "
TIMELINE_PREFIX_STYLE = "#8B8682"
APPROVAL_STYLE = "bold #ff9d32"


class StepKind(str, Enum):
    NARRATION = "narration"
    TOOL = "tool"
    ANSWER = "answer"
    APPROVAL = "approval"


@dataclass
class TurnStep:
    kind: StepKind
    text: str = ""
    tool_name: str | None = None
    tool_arguments: Mapping[str, object] | None = None


@dataclass
class TurnStepLog:
    """Append-only timeline for one live assistant turn."""

    tools_by_name: dict[str, Tool] = field(default_factory=dict)
    steps: list[TurnStep] = field(default_factory=list)
    _text_buffer: str = ""
    _is_thinking: bool = False
    _tool_call_count: int = 0
    _spinner: Spinner = field(default_factory=Spinner)

    @classmethod
    def from_tools(cls, tools: tuple[Tool, ...]) -> "TurnStepLog":
        return cls(tools_by_name={tool.name: tool for tool in tools})

    def feed(self, event: AgentEvent) -> None:
        if event.state == AgentState.THINKING:
            self._is_thinking = True
            return
        self._is_thinking = False
        if event.state == AgentState.RESPONDING and event.content is not None:
            self._on_responding(event.content)
            return
        if event.state == AgentState.RUNNING_TOOL:
            self._on_running_tool(event.tool_name, event.tool_arguments)
            return
        if event.state == AgentState.AWAITING_APPROVAL:
            self._on_awaiting_approval(event.tool_name, event.tool_arguments)
            return
        if event.state == AgentState.COMPLETED:
            self._on_completed()

    def answer_content(self) -> str:
        parts: list[str] = []
        for step in self.steps:
            if step.kind == StepKind.ANSWER:
                parts.append(step.text)
        return "".join(parts)

    def has_timeline_rows(self) -> bool:
        return any(
            step.kind in {StepKind.TOOL, StepKind.APPROVAL}
            for step in self.steps
        )

    def _on_responding(self, chunk: str) -> None:
        self._text_buffer += chunk

    def _on_running_tool(
        self,
        tool_name: str | None,
        tool_arguments: Mapping[str, object] | None,
    ) -> None:
        self._flush_buffer(as_answer=False)
        self._tool_call_count += 1
        self.steps.append(
            TurnStep(
                kind=StepKind.TOOL,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
            )
        )

    def _on_awaiting_approval(
        self,
        tool_name: str | None,
        tool_arguments: Mapping[str, object] | None,
    ) -> None:
        self._flush_buffer(as_answer=False)
        self._tool_call_count += 1
        self.steps.append(
            TurnStep(
                kind=StepKind.APPROVAL,
                text="⚠ Approval required",
                tool_name=tool_name,
                tool_arguments=tool_arguments,
            )
        )

    def _on_completed(self) -> None:
        self._flush_buffer(as_answer=True)

    def _flush_buffer(self, *, as_answer: bool) -> None:
        if not self._text_buffer:
            return
        kind = StepKind.ANSWER if as_answer else StepKind.NARRATION
        text = self._text_buffer
        self._text_buffer = ""
        if (
            as_answer
            and self.steps
            and self.steps[-1].kind == StepKind.ANSWER
        ):
            self.steps[-1].text += text
            return
        self.steps.append(TurnStep(kind=kind, text=text))

    def _render_tool_step(self, step: TurnStep) -> Text:
        tool_name = step.tool_name or "unknown"
        tool = self.tools_by_name.get(tool_name)
        if tool is None:
            line = Text()
            line.append("⚙ Running tool — ", style="bold #6ec1ff")
            line.append(tool_name, style="bold #6ec1ff")
            return line
        return format_tool_line(tool, step.tool_arguments)


def timeline_line(body: Text) -> Text:
    """Prefix a timeline row with the Hermes-style dashed marker."""
    return Text.assemble(
        (TIMELINE_PREFIX, TIMELINE_PREFIX_STYLE),
        body,
    )


def _print_timeline_rows(console: Console, step_log: TurnStepLog) -> None:
    for step in step_log.steps:
        if step.kind == StepKind.TOOL:
            console.print(timeline_line(step_log._render_tool_step(step)))
            continue
        if step.kind == StepKind.APPROVAL:
            console.print(
                timeline_line(Text(step.text, style=APPROVAL_STYLE))
            )
            if step.tool_name:
                tool = step_log.tools_by_name.get(step.tool_name)
                if tool is not None:
                    console.print(
                        timeline_line(
                            format_tool_line(tool, step.tool_arguments)
                        )
                    )


def render_persisted_turn(console: Console, step_log: TurnStepLog) -> None:
    """Write the full turn (timeline + answer box) to terminal scrollback."""
    if step_log._is_thinking:
        console.print(timeline_line(step_log._spinner.render()))
    if step_log.has_timeline_rows():
        _print_timeline_rows(console, step_log)
    answer_text = step_log.answer_content()
    if step_log._text_buffer:
        answer_text = answer_text + step_log._text_buffer
    if answer_text:
        if step_log.has_timeline_rows():
            console.print()
        render_answer_box(console, answer_text)
    console.print()


def render_live_turn(
    step_log: TurnStepLog,
    width: int,
    *,
    approval_request: ApprovalRequest | None = None,
    approval_panel_renderer=None,
) -> str:
    """Render the live assistant turn timeline as ANSI text."""

    output = StringIO()
    view_console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        width=max(20, width),
    )
    if step_log._is_thinking:
        view_console.print(timeline_line(step_log._spinner.render()))
    if step_log.has_timeline_rows():
        _print_timeline_rows(view_console, step_log)

    answer_text = step_log.answer_content()
    if step_log._text_buffer:
        answer_text = answer_text + step_log._text_buffer
    if answer_text:
        if step_log.has_timeline_rows():
            view_console.print()
        box_output = StringIO()
        box_console = Console(
            file=box_output,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            width=max(20, width),
        )
        render_answer_box(box_console, answer_text)
        view_console.file.write(box_output.getvalue())

    if approval_request is not None and approval_panel_renderer is not None:
        view_console.print()
        view_console.print(approval_panel_renderer(approval_request))

    view_console.print()
    return output.getvalue()


def render_steps_for_chat_view(
    steps: list[TurnStep],
    tools_by_name: dict[str, Tool],
    width: int,
    *,
    content: str = "",
    approval_request: ApprovalRequest | None = None,
    approval_panel_renderer=None,
) -> str:
    """Render a persisted assistant entry that includes a step timeline."""

    log = TurnStepLog(tools_by_name=tools_by_name, steps=list(steps))
    if content and not any(step.kind == StepKind.ANSWER for step in steps):
        log.steps.append(TurnStep(kind=StepKind.ANSWER, text=content))
    elif content:
        for step in reversed(log.steps):
            if step.kind == StepKind.ANSWER:
                if not step.text:
                    step.text = content
                break
        else:
            log.steps.append(TurnStep(kind=StepKind.ANSWER, text=content))
    return render_live_turn(
        log,
        width,
        approval_request=approval_request,
        approval_panel_renderer=approval_panel_renderer,
    )
