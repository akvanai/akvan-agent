"""Branding, Markdown, message, and input rendering helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from io import StringIO
from pathlib import Path

import time

from rich.console import Console, Group


def print_error(console: Any, message: str) -> None:
    """Print an error message without unsupported Console.print(stderr=True)."""
    target = getattr(console, "stderr", None)
    if isinstance(target, Console):
        target.print(message)
    else:
        console.print(message)
from agent.ui.completers import SlashCommandAutoSuggest, SlashCommandCompleter

if TYPE_CHECKING:
    from agent.session import AgentSession

from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.styles import Style

from agent import __version__
from agent.tools import AVAILABLE_TOOLS, Tool
from agent.skills.models import Skill

USER_MESSAGE_PREFIX = "❯❯❯ "
SOLID_WORDMARK = (
    " █████╗ ██╗  ██╗██╗   ██╗ █████╗ ███╗   ██╗",
    "██╔══██╗██║ ██╔╝██║   ██║██╔══██╗████╗  ██║",
    "███████║█████╔╝ ██║   ██║███████║██╔██╗ ██║",
    "██╔══██║██╔═██╗ ╚██╗ ██╔╝██╔══██║██║╚██╗██║",
    "██║  ██║██║  ██╗ ╚████╔╝ ██║  ██║██║ ╚████║",
    "╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝",
)
WORDMARK_COLOR = "#cc7700"  # muted orange, single color — less coloric
PROMPT_INPUT_BG = "#121212"
TEXT_MUTED = "#888888"
TEXT_LABEL = "#aaaaaa"
TEXT_VALUE = "#e8e8e8"
PROMPT_INPUT_STYLE = Style.from_dict(
    {
        "text-area": f"bg:{PROMPT_INPUT_BG} {TEXT_VALUE}",
        "input-area": f"bg:{PROMPT_INPUT_BG}",
        "input-shell": f"bg:{PROMPT_INPUT_BG}",
        "text-area.prompt": "bold #ff9d32",
        "status-accent": "bold #ff8800",
        "status-value": TEXT_VALUE,
        "status-muted": TEXT_MUTED,
        "suggestion": TEXT_MUTED,
        "completion-menu": f"bg:{PROMPT_INPUT_BG} border:{TEXT_MUTED}",
        "completion-menu.completion": TEXT_VALUE,
        "completion-menu.completion.current": f"bg:#2a2a2a bold {TEXT_VALUE}",
        "completion-menu.meta.completion": TEXT_MUTED,
        "completion-menu.multi-column-meta": TEXT_MUTED,
    }
)


class Spinner:
    """Terminal spinner with geeky braille frames."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    STYLE = "#ff8800"

    def __init__(self, label: str = "Thinking") -> None:
        self.label = label
        self._start = time.monotonic()

    @property
    def frame(self) -> str:
        elapsed = time.monotonic() - self._start
        return self.FRAMES[int(elapsed * 10) % len(self.FRAMES)]

    def render(self) -> Text:
        return Text(f"{self.frame} {self.label}…", style=self.STYLE)


class StreamingMarkdownRenderer:
    """Stream Markdown inside one colored, full-width assistant box."""

    BORDER_STYLE = "#ff8800"  # orange

    def __init__(self, console: Console) -> None:
        self.console = console
        self.pending = ""
        self.started = False
        self.code_fence = ""
        self.code_lines: list[str] = []
        self.width = max(20, console.width)
        self.inner_width = self.width - 4
        self.render_buffer = StringIO()
        self.render_console = Console(
            file=self.render_buffer,
            force_terminal=console.is_terminal,
            color_system=console.color_system,
            highlight=True,
            width=self.inner_width,
        )

    def feed(self, chunk: str) -> None:
        self._open_box()
        self.pending += chunk
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self._render_line(line)
        self._flush_wrapped_line()

    def _open_box(self) -> None:
        if self.started:
            return
        label = " AKVAN "
        fill = max(0, self.width - len(label) - 3)
        self.console.print(
            Text("╭─" + label + "─" * fill + "╮", style=self.BORDER_STYLE)
        )
        self.started = True
        self._emit_box_line("")

    def _flush_wrapped_line(self) -> None:
        if self.code_fence:
            return
        while len(self.pending) > self.inner_width:
            split_at = self.pending.rfind(" ", 0, self.inner_width + 1)
            if split_at < 1:
                split_at = self.inner_width
            self._render_line(self.pending[:split_at])
            self.pending = self.pending[split_at:].lstrip()

    def finish(self) -> None:
        self._open_box()
        if self.pending:
            self._render_line(self.pending)
            self.pending = ""
        self._finish_open_code_block()
        self._close_box()

    def abort(self) -> None:
        if self.pending:
            self._render_line(self.pending)
            self.pending = ""
        self._finish_open_code_block()
        if self.started:
            self._close_box()

    def _close_box(self) -> None:
        self._emit_box_line("")
        self.console.print(
            Text("╰" + "─" * (self.width - 2) + "╯", style=self.BORDER_STYLE)
        )
        self.started = False

    def _finish_open_code_block(self) -> None:
        if not self.code_fence:
            return
        source = "\n".join([self.code_fence, *self.code_lines, "```"])
        self._emit_renderable(
            Markdown(source, code_theme="ansi_dark", hyperlinks=False)
        )
        self.code_fence = ""
        self.code_lines.clear()

    def _render_line(self, line: str) -> None:
        if line.lstrip().startswith("```"):
            if self.code_fence:
                self._finish_open_code_block()
            else:
                self.code_fence = line.strip()
            return
        if self.code_fence:
            self.code_lines.append(line)
            return
        self._emit_renderable(
            Markdown(line or " ", code_theme="ansi_dark", hyperlinks=False)
        )

    def _emit_renderable(self, renderable) -> None:
        self.render_buffer.seek(0)
        self.render_buffer.truncate()
        self.render_console.print(renderable)
        rendered = self.render_buffer.getvalue().rstrip("\n")
        lines = rendered.split("\n") if rendered else [""]
        border = "\x1b[38;2;255;136;0m" if self.console.color_system else ""
        reset = "\x1b[0m" if border else ""

        for line in lines:
            self._emit_box_line(line, border=border, reset=reset)

    def _emit_box_line(
        self,
        line: str,
        *,
        border: str | None = None,
        reset: str | None = None,
    ) -> None:
        if border is None:
            border = "\x1b[38;2;255;136;0m" if self.console.color_system else ""
        if reset is None:
            reset = "\x1b[0m" if border else ""
        visible_width = Text.from_ansi(line).cell_len
        padding = " " * max(0, self.inner_width - visible_width)
        self.console.file.write(
            f"{border}│{reset} {line}{padding} {border}│{reset}\n"
        )
        self.console.file.flush()


def build_markdown_panel(title: str, content: str) -> Group:
    """Build a quiet, borderless assistant response."""
    return Group(
        Text(title, style="bold #ff8800"),
        Markdown(content or "...", code_theme="ansi_dark", hyperlinks=False),
    )


def build_brand_art() -> Text:
    """Small, single-color ASCII wordmark — no rainbow logo block."""
    art = Text()
    for line in SOLID_WORDMARK:
        art.append(line + "\n", style=WORDMARK_COLOR)
    return art


def render_compact_header(
    console: Console,
    *,
    provider_name: str,
    model: str,
    max_iterations: int = 30,
    tools: tuple[Tool, ...] = (),
    skills: tuple[Skill, ...] = (),
    cwd: Path | None = None,
    enabled_toolsets: tuple[str, ...] = (),
    clear: bool = True,
) -> None:
    """Print a compact startup banner, clearing the terminal by default."""
    if clear and console.is_terminal:
        console.clear()
    brand = Text()
    brand.append(" AKVAN ", style="bold #14100c on #ff8800")
    brand.append(f" v{__version__}", style=f"bold {TEXT_VALUE}")
    console.print(brand)
    console.print()

    session = Text()
    session.append("Model       ", style=TEXT_LABEL)
    session.append(f"{model}\n", style=f"bold {TEXT_VALUE}")
    session.append("Provider    ", style=TEXT_LABEL)
    session.append(f"{provider_name}\n", style=TEXT_VALUE)
    session.append("Iterations  ", style=TEXT_LABEL)
    session.append(f"{max_iterations}", style=TEXT_VALUE)
    console.print(session)
    console.print()

    if cwd is not None:
        workspace = Text()
        workspace.append("Workspace   ", style=TEXT_LABEL)
        workspace.append(str(cwd), style=TEXT_MUTED)
        console.print(workspace)
        console.print()

    capabilities = Text()
    capabilities.append("Tools       ", style=TEXT_LABEL)
    capabilities.append(str(len(tools)), style=TEXT_VALUE)
    if enabled_toolsets:
        capabilities.append(f" ({', '.join(enabled_toolsets)})", style=TEXT_MUTED)
    capabilities.append("\n")
    capabilities.append("Skills      ", style=TEXT_LABEL)
    capabilities.append(str(len(skills)), style=TEXT_VALUE)
    if skills:
        preview = ", ".join(f"/{skill.name}" for skill in skills[:4])
        if len(skills) > 4:
            preview += f", +{len(skills) - 4} more"
        capabilities.append(f" ({preview})", style=TEXT_MUTED)
    console.print(capabilities)
    console.print()

    console.print(
        Text(
            "/learn · /skills · /reload · /exit · Esc+Enter newline",
            style=TEXT_MUTED,
        )
    )
    console.print()


def render_header(
    console: Console,
    *,
    provider_name: str,
    model: str,
    max_iterations: int,
    tools: tuple[Tool, ...] = AVAILABLE_TOOLS,
    skills: tuple[Skill, ...] = (),
    clear: bool = True,
) -> None:
    if clear:
        console.clear()
    body = build_brand_art()
    body.append("\n")
    body.append("Akvan Agent", style="bold white")
    body.append(f" v{__version__}\n\n", style="#ff8800")
    body.append("Model     ", style="#cc7700")
    body.append(f"{model}\n", style="bold #ffff00")
    body.append("Provider  ", style="#cc7700")
    body.append(f"{provider_name}\n\n", style="#00ff00")

    body.append("Tools\n", style="bold #cc7700")
    if tools:
        for tool in tools:
            body.append("  • ", style="#ff8800")
            body.append(tool.name, style="bold #ff8800")
            body.append(f" — {tool.description}\n", style="white")
    else:
        body.append("  None\n", style="dim white")

    body.append("\nSkills\n", style="bold #cc7700")
    if skills:
        for skill in skills:
            body.append("  • ", style="#ff8800")
            body.append(f"/{skill.name}", style="bold #ff8800")
            body.append(f" — {skill.description} ({skill.origin})\n", style="white")
    else:
        body.append("  None\n", style="dim white")

    console.print(
        Panel(
            body,
            title="AKVAN AGENT",
            border_style="#ff8800",
            expand=True,
            padding=(1, 3),
        )
    )
    console.print(Rule(style="#ff8800"))


def _context_percent_from_session(session: AgentSession | None) -> float | None:
    if session is None:
        return None
    return session.loop.context_usage(session.messages).percentage


def build_status_strip(
    provider_name: str,
    model: str,
    max_iterations: int,
    *,
    context_percent: float | None = None,
) -> Text:
    strip = Text()
    strip.append(model, style="bold #ffff00")
    strip.append(" │ ", style="#cc7700")
    strip.append(provider_name, style="#00ff00")
    if context_percent is not None:
        strip.append(" │ ", style="#cc7700")
        strip.append(f"ctx {context_percent:.0f}%", style="#00ff00")
    return strip


def ask_user(
    console: Console,
    *,
    model: str,
    provider_name: str,
    max_iterations: int,
    transcript: list[tuple[str, str]],
    session: AgentSession | None = None,
) -> str:
    if not console.is_terminal:
        console.print(Rule(style="#ff8800"))
        console.print(
            build_status_strip(
                provider_name,
                model,
                0,
                context_percent=_context_percent_from_session(session),
            )
        )
        return console.input("❯ ")

    return run_prompt_footer(
        console,
        model=model,
        provider_name=provider_name,
        session=session,
    )


def build_prompt_input_shell(input_area: TextArea) -> HSplit:
    return HSplit([input_area], style="class:input-shell")


def build_completions_panel(input_area: TextArea) -> CompletionsMenu:
    """Drop-up completion list rendered directly above the prompt input."""
    return CompletionsMenu(
        max_height=12,
        scroll_offset=1,
        extra_filter=has_focus(input_area.buffer),
    )


def run_prompt_footer(
    console: Console,
    *,
    model: str,
    provider_name: str,
    session: AgentSession | None = None,
) -> str:
    prompt_text = "❯ "
    completer = SlashCommandCompleter(session) if session is not None else None
    auto_suggest = SlashCommandAutoSuggest(session) if session is not None else None
    input_area = TextArea(
        height=lambda: Dimension.exact(estimate_input_height(input_area.text, prompt_text, console.size.width)),
        multiline=True,
        prompt=prompt_text,
        wrap_lines=True,
        dont_extend_height=True,
        style="class:input-area",
        completer=completer,
        auto_suggest=auto_suggest,
        complete_while_typing=True,
    )
    bindings = KeyBindings()

    @bindings.add("tab")
    def _(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            completion = buffer.complete_state.current_completion
            if completion is None and buffer.complete_state.completions:
                buffer.go_to_completion(0)
                completion = buffer.complete_state.current_completion
            if completion is not None:
                buffer.apply_completion(completion)
            return
        if buffer.suggestion:
            buffer.accept_suggestion()
        elif buffer.completer and buffer.text.lstrip().startswith("/"):
            buffer.start_completion(select_first=True)

    @bindings.add("down")
    @bindings.add("c-n")
    def _(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_next()

    @bindings.add("up")
    @bindings.add("c-p")
    def _(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()

    def submit(event) -> None:
        text = event.current_buffer.text
        if text.strip():
            event.app.exit(result=text)

    @bindings.add("enter")
    def _(event) -> None:
        submit(event)

    def insert_newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("escape", "enter")
    def _(event) -> None:
        insert_newline(event)

    # Many terminals/IDEs encode Shift+Enter or Ctrl+Enter as LF.
    @bindings.add("c-j")
    def _(event) -> None:
        insert_newline(event)

    @bindings.add("c-c")
    def _(event) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    @bindings.add("c-d")
    def _(event) -> None:
        if event.current_buffer.text:
            submit(event)
            return
        event.app.exit(exception=EOFError)

    context_percent = _context_percent_from_session(session)
    status_bar = Window(
        content=FormattedTextControl(
            lambda: plain_status_fragments(
                provider_name, model, context_percent=context_percent
            )
        ),
        height=1,
        wrap_lines=False,
    )
    input_shell = build_prompt_input_shell(input_area)
    completions_panel = (
        build_completions_panel(input_area) if session is not None else Window(height=0)
    )

    layout = Layout(
        HSplit(
            [
                Window(height=0),
                Window(),
                status_bar,
                completions_panel,
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
    # Keep scrollback messages separated from the status strip above the input.
    console.print()
    console.print()
    return app.run()


def plain_status_fragments(
    provider_name: str,
    model: str,
    *,
    context_percent: float | None = None,
):
    fragments = [
        ("class:status-value", model),
        ("class:status-muted", " │ "),
        ("class:status-value", provider_name),
    ]
    if context_percent is not None:
        fragments.extend(
            [
                ("class:status-muted", " │ "),
                ("class:status-value", f"ctx {context_percent:.0f}%"),
            ]
        )
    fragments.append(("class:status-muted", " │ Esc+Enter newline"))
    return fragments


def estimate_input_height(text: str, prompt_text: str, terminal_columns: int, max_height: int = 8) -> int:
    columns = max(1, int(terminal_columns or 80))
    prompt_width = len(prompt_text or "")
    rows = 0
    for index, line in enumerate((text or "").split("\n") or [""]):
        width = len(line) + (prompt_width if index == 0 else 0)
        rows += max(1, (width + columns - 1) // columns)
    return min(max(rows, 1), max_height)


def render_user_message(console: Console, content: str) -> None:
    console.print()
    console.print(Rule(style="#ff8800"))
    message = Text()
    lines = content.splitlines() or [""]
    for index, line in enumerate(lines):
        prefix = (
            USER_MESSAGE_PREFIX if index == 0 else " " * len(USER_MESSAGE_PREFIX)
        )
        message.append(prefix, style="#ff8800")
        message.append(line, style="white")
        if index < len(lines) - 1:
            message.append("\n")
    console.print(message)
    console.print()


def render_markdown_message(console: Console, title: str, content: str) -> None:
    console.print(build_markdown_panel(title, content))


def render_answer_box(console: Console, content: str) -> None:
    """Render assistant markdown inside the orange AKVAN response box."""
    renderer = StreamingMarkdownRenderer(console)
    renderer.feed(content or "...")
    renderer.finish()


def render_message(
    console: Console,
    title: str,
    content: str,
    *,
    border_style: str,
) -> None:
    console.print(
        Panel(
            content,
            title=f"[bold]{title}[/]",
            border_style=border_style,
            expand=True,
            padding=(1, 2),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
