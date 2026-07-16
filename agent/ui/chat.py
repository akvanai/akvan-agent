"""Interactive chat application and turn rendering."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
import threading

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea

from agent.agent import AgentLoop
from agent.events import AgentState
from agent.messages import Message, TurnContext
from agent.session import AgentSession
from agent.tools.approval import ApprovalChoice, ApprovalRequest
from agent.ui.commands import SessionCommandKind, resolve_input
from agent.ui.rendering import (
    PROMPT_INPUT_STYLE,
    ask_user,
    build_prompt_input_shell,
    estimate_input_height,
    render_markdown_message,
    render_user_message,
)
from agent.ui.steps import (
    StepKind,
    TurnStep,
    TurnStepLog,
    render_live_turn,
    render_persisted_turn,
    render_steps_for_chat_view,
)


class _ApprovalController:
    """Bridge a worker-thread approval request to terminal key bindings."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._condition = threading.Condition()
        self._request: ApprovalRequest | None = None
        self._choice: ApprovalChoice | None = None
        self._on_change = on_change or (lambda: None)

    def set_on_change(self, callback: Callable[[], None]) -> None:
        self._on_change = callback

    @property
    def request(self) -> ApprovalRequest | None:
        with self._condition:
            return self._request

    def ask(self, request: ApprovalRequest, timeout: int) -> ApprovalChoice:
        with self._condition:
            self._request = request
            self._choice = None
            self._on_change()
            answered = self._condition.wait_for(
                lambda: self._choice is not None, timeout=timeout
            )
            choice = self._choice
            self._request = None
            self._choice = None
            self._on_change()
        if not answered or choice is None:
            raise TimeoutError("approval timed out")
        return choice

    def choose(self, choice: ApprovalChoice) -> bool:
        with self._condition:
            if self._request is None or choice not in self._request.choices:
                return False
            self._choice = choice
            self._condition.notify_all()
            return True

    def choose_index(self, index: int) -> bool:
        with self._condition:
            if (
                self._request is None
                or not 0 <= index < len(self._request.choices)
            ):
                return False
            self._choice = self._request.choices[index]
            self._condition.notify_all()
            return True

    def cancel(self) -> None:
        self.choose(ApprovalChoice.DENY)


def _approval_panel(request: ApprovalRequest) -> Panel:
    labels = {
        ApprovalChoice.ONCE: "Allow once",
        ApprovalChoice.SESSION: "Allow for this session",
        ApprovalChoice.ALWAYS: "Always allow this exact operation",
        ApprovalChoice.DENY: "Deny",
    }
    body = Text()
    body.append("OPERATION\n", style="bold #ffb454")
    body.append(request.summary, style="bold #fff3e0")
    body.append("\n\nREASON\n", style="bold #ffb454")
    body.append(request.reason, style="#ffd6a0")
    body.append("\n\nCHOOSE AN ACTION\n", style="bold #ffb454")
    for index, choice in enumerate(request.choices, start=1):
        body.append(f" {index} ", style="bold #21170f on #ff9d32")
        body.append(f"  {labels[choice]}\n", style="#fff3e0")
    body.append("\nPress a number", style="#bfa98f")
    body.append("  •  y", style="bold #ffb454")
    body.append(" allow once", style="#bfa98f")
    body.append("  •  n/d", style="bold #ffb454")
    body.append(" deny", style="#bfa98f")
    return Panel(
        body,
        title="[bold #21170f on #ff9d32] APPROVAL REQUIRED [/]",
        title_align="left",
        border_style="bold #ff9d32",
        style="#fff3e0 on #21170f",
        padding=(1, 2),
        expand=True,
    )


def _status_line(
    model: str,
    provider_name: str,
    label: str,
    cost_usd: float | None,
    *,
    context_percent: float | None = None,
) -> str:
    parts = [model, provider_name]
    if context_percent is not None:
        parts.append(f"ctx {context_percent:.0f}%")
    if cost_usd is not None:
        parts.append("session " + chr(36) + format(cost_usd, ".6f"))
    parts.append(label)
    return " │ ".join(parts)


def render_streaming_response(
    console: Console,
    loop: AgentLoop,
    messages: list[Message],
    user_input: str,
    *,
    turn_context: TurnContext | None = None,
) -> str:
    """Stream in a temporary bottom-anchored UI, then persist the answer."""

    if not console.is_terminal:
        step_log = TurnStepLog.from_tools(loop.tools)
        for event in loop.stream_events(
            messages, user_input, turn_context=turn_context
        ):
            step_log.feed(event)
        content = step_log.answer_content()
        render_persisted_turn(console, step_log)
        return content

    step_log = TurnStepLog.from_tools(loop.tools)
    error: Exception | None = None
    lock = threading.Lock()
    approvals = _ApprovalController()
    spinner_running = threading.Event()

    def response_fragments():
        with lock:
            log = step_log
        request = approvals.request
        rendered = render_live_turn(
            log,
            console.size.width - 1,
            approval_request=request,
            approval_panel_renderer=_approval_panel,
        )
        return list(to_formatted_text(ANSI(rendered)))

    def status_fragments():
        with lock:
            log = step_log
        request = approvals.request
        if request is not None:
            label = f"approval: press 1–{len(request.choices)}"
        elif log._text_buffer or any(
            step.kind == StepKind.ANSWER for step in log.steps
        ):
            label = "writing"
        elif log.steps and log.steps[-1].kind == StepKind.TOOL:
            label = f"tool: {log.steps[-1].tool_name or 'unknown'}"
        else:
            label = "thinking"
        return [("", _status_line(
            loop.model,
            loop.provider.name,
            label,
            loop.session_cost_usd,
            context_percent=loop.context_usage(messages).percentage,
        ))]

    response_window = Window(
        content=FormattedTextControl(response_fragments),
        wrap_lines=False,
    )
    message_spacer = Window(
        content=FormattedTextControl(lambda: []),
        height=2,
        wrap_lines=False,
    )
    status_bar = Window(
        content=FormattedTextControl(status_fragments),
        height=1,
        wrap_lines=False,
    )
    input_area = TextArea(
        height=1,
        prompt="❯ ",
        read_only=True,
        dont_extend_height=True,
        style="class:input-area",
    )
    input_shell = build_prompt_input_shell(input_area)
    bindings = KeyBindings()

    @bindings.add("c-c")
    def _(event) -> None:
        approvals.cancel()
        event.app.exit(exception=KeyboardInterrupt)

    for number in range(1, 5):
        def choose_number(event, index=number - 1) -> None:
            if approvals.choose_index(index):
                event.app.invalidate()

        bindings.add(str(number))(choose_number)

    @bindings.add("y")
    def _(event) -> None:
        if approvals.choose(ApprovalChoice.ONCE):
            event.app.invalidate()

    @bindings.add("n")
    @bindings.add("d")
    def _(event) -> None:
        if approvals.choose(ApprovalChoice.DENY):
            event.app.invalidate()

    layout = Layout(
        HSplit(
            [
                response_window,
                message_spacer,
                status_bar,
                input_shell,
            ]
        ),
        focused_element=input_area,
    )
    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=PROMPT_INPUT_STYLE,
        full_screen=False,
        mouse_support=False,
        erase_when_done=True,
    )
    approvals.set_on_change(app.invalidate)

    def process_turn() -> None:
        nonlocal error
        spinner_running.set()
        try:
            for event in loop.stream_events(
                messages, user_input, turn_context=turn_context
            ):
                with lock:
                    step_log.feed(event)
                    if step_log._is_thinking:
                        spinner_running.set()
                    else:
                        spinner_running.clear()
                app.invalidate()
        except Exception as exc:
            error = exc
        finally:
            spinner_running.clear()
            app.loop.call_soon_threadsafe(lambda: app.exit(result=None))

    def _invalidate_spinner() -> None:
        while spinner_running.is_set():
            app.loop.call_soon_threadsafe(app.invalidate)
            spinner_running.wait(0.08)

    app.pre_run_callables.append(
        lambda: threading.Thread(target=process_turn, daemon=True).start()
    )
    app.pre_run_callables.append(
        lambda: threading.Thread(target=_invalidate_spinner, daemon=True).start()
    )
    loop.approval_manager.set_callback(approvals.ask)
    try:
        app.run()
    finally:
        approvals.cancel()
        loop.approval_manager.set_callback(None)

    with lock:
        turn_error = error
        content = step_log.answer_content()
    if turn_error is not None:
        raise turn_error
    render_persisted_turn(console, step_log)
    return content


def _legacy_steps_from_entry(entry: dict[str, object]) -> list[TurnStep] | None:
    """Build a minimal step timeline from pre-timeline assistant entries."""

    state = entry.get("state")
    if state is None:
        return None
    steps: list[TurnStep] = []
    if state == AgentState.THINKING:
        return None
    elif state == AgentState.RUNNING_TOOL:
        steps.append(
            TurnStep(
                kind=StepKind.TOOL,
                tool_name=str(entry.get("tool_name") or "unknown"),
            )
        )
    elif state == AgentState.AWAITING_APPROVAL:
        steps.append(
            TurnStep(kind=StepKind.APPROVAL, text="⚠ Approval required")
        )
    else:
        return None
    return steps


def render_chat_view(entries: list[dict[str, object]], width: int) -> str:
    """Render the message log without card borders."""
    output = StringIO()
    view_console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        width=max(20, width),
    )
    for entry in entries:
        role = str(entry["role"])
        content = str(entry["content"])
        if role == "user":
            view_console.print()
            message = Text()
            lines = content.splitlines() or [""]
            available = max(1, width - 4)
            for index, line in enumerate(lines):
                prefix = " ❯ " if index == 0 else "   "
                visible = prefix + line
                message.append(prefix, style="bold #ff9d32 on #2a2119")
                message.append(line, style="white on #2a2119")
                message.append(
                    " " * max(1, available - len(visible)),
                    style="on #2a2119",
                )
                if index < len(lines) - 1:
                    message.append("\n")
            view_console.print(message)
            view_console.print()
            continue

        approval_request = entry.get("approval_request")
        raw_steps = entry.get("steps")
        tools_by_name = entry.get("tools_by_name")
        steps: list[TurnStep] | None = None
        if isinstance(raw_steps, list):
            steps = [step for step in raw_steps if isinstance(step, TurnStep)]
        if steps is None:
            steps = _legacy_steps_from_entry(entry)
        if steps is not None:
            tool_map = (
                tools_by_name
                if isinstance(tools_by_name, dict)
                else {}
            )
            rendered = render_steps_for_chat_view(
                steps,
                tool_map,
                width,
                content=content,
                approval_request=(
                    approval_request
                    if isinstance(approval_request, ApprovalRequest)
                    else None
                ),
                approval_panel_renderer=_approval_panel,
            )
            view_console.file.write(rendered)
            continue

        view_console.print(
            Markdown(
                content or "...",
                code_theme="ansi_dark",
                hyperlinks=False,
            )
        )
        view_console.print()
    return output.getvalue()


def _run_plain_session(console: Console, session: AgentSession) -> int:
    transcript: list[tuple[str, str]] = []
    while True:
        try:
            user_input = ask_user(
                console,
                model=session.model,
                provider_name=session.provider.name,
                max_iterations=session.max_iterations,
                transcript=transcript,
                session=session,
            ).strip()
        except (KeyboardInterrupt, EOFError):
            return 0
        if not user_input:
            continue
        command = resolve_input(session, user_input)
        if command.kind == SessionCommandKind.EXIT:
            session.end()
            return 0
        render_user_message(console, user_input)
        if command.kind == SessionCommandKind.RELOAD:
            snapshot = session.reload()
            render_markdown_message(
                console,
                "AKVAN",
                f"Prompt reloaded (`{snapshot.fingerprint[:12]}`). "
                f"{len(snapshot.skills.skills)} skills available.",
            )
            continue
        if command.kind == SessionCommandKind.RESUME:
            error = session.resume(command.message or "")
            if error:
                render_markdown_message(console, "AKVAN", error)
            else:
                render_markdown_message(
                    console,
                    "AKVAN",
                    f"Resumed session `{session.persistence.session_id[:8]}` "
                    f"({len(session.messages) - 1} messages loaded).",
                )
            continue
        if command.kind in {
            SessionCommandKind.SKILLS,
            SessionCommandKind.KNOWLEDGE,
            SessionCommandKind.SESSIONS,
            SessionCommandKind.YOLO,
            SessionCommandKind.COMPRESS,
            SessionCommandKind.USAGE,
            SessionCommandKind.ERROR,
        }:
            render_markdown_message(console, "AKVAN", command.message or "")
            continue
        session.begin_turn()
        turn_start = len(session.messages)
        answer = render_streaming_response(
            console,
            session.loop,
            session.messages,
            command.raw_input,
            turn_context=command.turn_context,
        )
        session.scan_turn_for_memory_tool_use(turn_start)
        session.scan_turn_for_skill_tool_use(turn_start)
        session.record_turn_tool_iterations(
            AgentSession.count_turn_tool_iterations(session.messages, turn_start)
        )
        session.persist_new_messages()

        def _notify(message: str | None) -> None:
            if not message:
                return
            if session.prompt.memory_config.memory_notifications == "off":
                return
            render_markdown_message(
                console, "AKVAN", f"Self-improvement review: {message}"
            )

        session.maybe_spawn_background_review(on_complete=_notify)
        transcript.extend((("user", user_input), ("assistant", answer)))


def run_interactive_session(
    console: Console,
    *,
    session: AgentSession,
) -> int:
    """Run chat in the normal terminal buffer with native scrollback."""

    return _run_plain_session(console, session)
