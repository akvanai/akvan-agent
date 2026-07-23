"""
Verifies the terminal interface through captured and simulated output.
Covers branding, Markdown panels, input sizing, chat history, and setup screens.
Protects the full-screen selector and fixed-composer behavior from regressions.
"""

from __future__ import annotations

from io import StringIO
import threading

import pytest

from rich.console import Console
from prompt_toolkit.application import Application

from agent import __version__
from agent.events import AgentState
from agent.messages import Completion
from agent.prompts import PromptBuilder
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.tools.approval import ApprovalChoice, ApprovalRequest
from agent.ui.app import build_parser
from agent.ui import chat
from agent.tools.base import Tool
from agent.tools.presentation import ToolPresentation
from agent.ui.chat import render_chat_view
from agent.ui.steps import StepKind, TurnStep
from agent.ui.rendering import (
    StreamingMarkdownRenderer,
    build_brand_art,
    build_status_strip,
    estimate_input_height,
    plain_status_fragments,
    print_error,
    render_compact_header,
    render_header,
    render_markdown_message,
)
from agent.ui.setup import run_full_screen_selector


def test_compact_header_includes_session_sections() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)

    render_compact_header(
        console,
        provider_name="deepseek",
        model="deepseek-v4-flash",
        max_iterations=30,
        tools=(),
        skills=(),
        cwd=None,
        enabled_toolsets=("coding",),
        clear=False,
    )

    rendered = output.getvalue()

    assert "AKVAN" in rendered
    assert "deepseek-v4-flash" in rendered
    assert "Provider" in rendered
    assert "Iterations" in rendered
    assert "Tools" in rendered
    assert "Skills" in rendered
    assert "/skills" in rendered
    assert "\n\n" in rendered


def test_cli_header_and_message_render() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)

    render_header(
        console,
        provider_name="openrouter",
        model="openai/gpt-4o-mini",
        max_iterations=30,
    )
    render_markdown_message(
        console,
        "AKVAN",
        "## Setup\n\n- install\n\n```bash\npip install django\n```",
    )

    rendered = output.getvalue()

    assert "AKVAN AGENT" in rendered
    assert f"Akvan Agent v{__version__}" in rendered
    assert "openai/gpt-4o-mini" in rendered
    assert "Tools" in rendered
    assert "Skills" in rendered
    assert "testy" not in rendered
    assert "None" in rendered
    assert "Commands" not in rendered
    assert "AKVAN" in rendered
    assert "Setup" in rendered
    assert "install" in rendered
    assert "pip install django" in rendered


def test_print_error_does_not_pass_stderr_keyword_to_console_print() -> None:
    class StrictConsole:
        stderr = None

        def __init__(self) -> None:
            self.messages: list[str] = []

        def print(self, message, **kwargs):
            if "stderr" in kwargs:
                raise TypeError("Console.print() got an unexpected keyword argument 'stderr'")
            self.messages.append(str(message))

    console = StrictConsole()

    print_error(console, "[bold #ff0000]Error:[/] boom")

    assert console.messages == ["[bold #ff0000]Error:[/] boom"]


def test_estimate_input_height_wraps_long_lines() -> None:
    assert estimate_input_height("hello", "❯ ", 80) == 1
    assert estimate_input_height("x" * 100, "❯ ", 40) > 1


def test_streaming_markdown_renderer_flushes_lines_and_code() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=50)
    renderer = StreamingMarkdownRenderer(console)

    renderer.feed("## Hello\n\nA streamed response.\n\n```python\n")
    renderer.feed("print(\"hello\")\n```")
    renderer.finish()

    rendered = output.getvalue()
    assert "AKVAN" in rendered
    assert "╭" in rendered
    assert "│" in rendered
    assert "╰" in rendered
    assert "Hello" in rendered
    assert "A streamed response." in rendered
    assert "print" in rendered
    assert "hello" in rendered
    assert "\x1b" not in rendered


def test_brand_art_uses_solid_logo_and_web_palette() -> None:
    art = build_brand_art()

    assert "█████" in art.plain
    assert "██" in art.plain
    styles = {str(span.style) for span in art.spans}
    assert any("#cc7700" in style for style in styles)


def test_model_setup_command_is_available() -> None:
    args = build_parser().parse_args(["model"])

    assert args.command == "model"


def test_model_selector_uses_full_screen_application(monkeypatch) -> None:
    observed: dict[str, bool] = {}

    def fake_run(self, *args, **kwargs):
        observed["full_screen"] = self.full_screen
        return "openrouter"

    monkeypatch.setattr(Application, "run", fake_run)
    result = run_full_screen_selector(
        title="Provider",
        subtitle="Choose",
        items=[("openrouter", "OpenRouter")],
        default="openrouter",
    )

    assert result == "openrouter"
    assert observed["full_screen"] is True


def test_chat_view_renders_without_writing_to_live_stdout() -> None:
    rendered = render_chat_view(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "**hi there**"},
        ],
        60,
    )

    assert "hello" in rendered
    assert "❯" in rendered
    assert "╭" not in rendered
    assert "╰" not in rendered
    assert "hi there" in rendered


def test_chat_view_renders_agent_activity_instead_of_placeholder_dots() -> None:
    thinking = render_chat_view(
        [{"role": "assistant", "content": "", "state": AgentState.THINKING}],
        80,
    )
    skill_view = Tool(
        name="skill_view",
        description="Load a skill.",
        parameters={"type": "object", "properties": {}},
        run=lambda name: name,
        presentation=ToolPresentation(
            emoji="📄",
            label="Loading skill",
            format_detail=lambda args: str(args.get("name", "")),
        ),
    )
    tool = render_chat_view(
        [
            {
                "role": "assistant",
                "content": "",
                "steps": [
                    TurnStep(
                        kind=StepKind.TOOL,
                        tool_name="skill_view",
                        tool_arguments={"name": "canvas"},
                    )
                ],
                "tools_by_name": {"skill_view": skill_view},
            }
        ],
        80,
    )

    assert "reasoning privately" not in thinking
    assert "Thinking" not in thinking
    assert "Loading skill" in tool
    assert "canvas" in tool
    assert "…" not in thinking



def test_chat_session_uses_normal_terminal_buffer(monkeypatch, tmp_path) -> None:
    class FakeProvider(Provider):
        name = "fake"

        def complete(self, messages, model, options=None):
            return Completion(message={"role": "assistant", "content": "ok"})

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    session = AgentSession.create(
        provider=FakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )
    application_started = False

    def fake_run(self, *args, **kwargs):
        nonlocal application_started
        application_started = True
        return 0

    monkeypatch.setattr(Application, "run", fake_run)
    monkeypatch.setattr(chat, "ask_user", lambda *args, **kwargs: "/exit")
    console = Console(file=StringIO(), force_terminal=True, width=80)

    assert chat.run_interactive_session(console, session=session) == 0
    assert application_started is False


def test_approval_controller_accepts_a_numbered_choice_and_clears_request() -> None:
    shown = threading.Event()
    controller = chat._ApprovalController(shown.set)
    request = ApprovalRequest(
        "request-1",
        "terminal",
        "rm -r generated",
        "recursive file deletion",
        (ApprovalChoice.ONCE, ApprovalChoice.SESSION, ApprovalChoice.DENY),
    )
    result: list[ApprovalChoice] = []
    worker = threading.Thread(
        target=lambda: result.append(controller.ask(request, 1))
    )

    worker.start()
    assert shown.wait(1)
    assert controller.request == request
    assert controller.choose_index(1)
    worker.join(1)

    assert not worker.is_alive()
    assert result == [ApprovalChoice.SESSION]
    assert controller.request is None


def test_approval_controller_times_out_closed() -> None:
    controller = chat._ApprovalController()
    request = ApprovalRequest(
        "request-1",
        "terminal",
        "dangerous command",
        "test risk",
        (ApprovalChoice.ONCE, ApprovalChoice.DENY),
    )

    with pytest.raises(TimeoutError, match="approval timed out"):
        controller.ask(request, 0)

    assert controller.request is None


def test_chat_view_renders_approval_choices() -> None:
    request = ApprovalRequest(
        "request-1",
        "terminal",
        "rm -r generated",
        "recursive file deletion",
        (ApprovalChoice.ONCE, ApprovalChoice.DENY),
    )

    rendered = render_chat_view(
        [
            {
                "role": "assistant",
                "content": "",
                "state": AgentState.AWAITING_APPROVAL,
                "tool_name": "terminal",
                "approval_request": request,
            }
        ],
        80,
    )

    assert "APPROVAL REQUIRED" in rendered
    assert "recursive file deletion" in rendered
    assert "OPERATION" in rendered
    assert "REASON" in rendered
    assert "CHOOSE AN ACTION" in rendered
    assert "Allow once" in rendered
    assert "Deny" in rendered
    assert rendered.index("Allow once") < rendered.index("Deny")


def test_status_line_only_shows_cost_when_provider_reported_it() -> None:
    without_cost = chat._status_line("model", "provider", "ready", None)
    with_cost = chat._status_line("model", "provider", "ready", 0.1234567)
    with_ctx = chat._status_line(
        "model", "provider", "ready", None, context_percent=12.4
    )

    assert "🤖" not in without_cost
    assert without_cost.startswith("model │ provider")
    assert "session" not in without_cost
    assert "session " + chr(36) + "0.123457" in with_cost
    assert "ctx 12%" in with_ctx
    assert "🤖" not in with_ctx


def test_plain_status_fragments_show_context_percent() -> None:
    fragments = plain_status_fragments(
        "provider", "model", context_percent=7.6
    )
    text = "".join(part for _, part in fragments)

    assert "🤖" not in text
    assert text.startswith("model │ provider │ ctx 8% │ Esc+Enter newline")


def test_build_status_strip_omits_context_when_unknown() -> None:
    without = build_status_strip("provider", "model", 0)
    with_ctx = build_status_strip(
        "provider", "model", 0, context_percent=41.2
    )

    assert "🤖" not in without.plain
    assert "ctx" not in without.plain
    assert "ctx 41%" in with_ctx.plain
    assert "🤖" not in with_ctx.plain


def test_interactive_session_keeps_all_turns_in_terminal_output(
    monkeypatch, tmp_path
) -> None:
    class HistoryProvider(Provider):
        name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, model, options=None):
            self.calls += 1
            return Completion(
                message={"role": "assistant", "content": f"answer {self.calls}"}
            )

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    session = AgentSession.create(
        provider=HistoryProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )
    inputs = iter(["first message", "second message", "/exit"])
    monkeypatch.setattr(chat, "ask_user", lambda *args, **kwargs: next(inputs))
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)

    assert chat.run_interactive_session(console, session=session) == 0

    rendered = output.getvalue()
    assert "first message" in rendered
    assert "answer 1" in rendered
    assert "second message" in rendered
    assert "answer 2" in rendered
