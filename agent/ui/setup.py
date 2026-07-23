"""Provider and model setup wizard."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import httpx
from rich.console import Console
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.styles import Style

from agent import __version__
from agent.config import (
    DEFAULT_AKVAN_BACKEND_URL,
    DEFAULT_CODEX_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    Settings,
    load_setup_settings,
    save_settings,
)
from agent.gateway.daemon import restart_running_gateways
from agent.ui.rendering import TEXT_LABEL, TEXT_VALUE
from agent.providers.base import ModelInfo, ProviderError
from agent.providers.akvan import AkvanProvider
from agent.providers.deepseek import DEFAULT_DEEPSEEK_MODELS, DeepSeekProvider
from agent.providers.openai_codex import (
    DEFAULT_CODEX_MODELS,
    OpenAICodexProvider,
    load_codex_cli_token,
)
from agent.providers.openrouter import OpenRouterProvider


PROVIDER_OPTIONS = (
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "One API key with access to models from many providers.",
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex",
        "description": "Use an OpenAI API key or an existing Codex CLI session.",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "description": "Direct DeepSeek API with V4 thinking-mode support.",
    },
    {
        "id": "akvan",
        "name": "Akvan",
        "description": "Sign in with OTP and use your Akvan plan credits.",
    },
)


SETUP_STYLE = Style.from_dict(
    {
        "setup": "bg:#0f1218",
        "chrome": "#46546b",
        "brand-badge": "bold fg:#14100c bg:#ff9f1c",
        "brand-version": "bold #ffd166",
        "title": "bold #ffd166",
        "subtitle": "#a7b0bf",
        "step": "bold #7dd3fc",
        "selected": "bg:#2a2418 bold #fff4c2",
        "selected-marker": "bold #ff9f1c",
        "item": "#e6edf3",
        "item-muted": "#8f9aad",
        "separator": "#324055",
        "line": "#324055",
        "hint": "#8f9aad",
        "hint-key": "bold #7dd3fc",
        "search-label": "bold #7dd3fc",
        "search-value": "#e6edf3",
        "message-label": TEXT_LABEL,
        "message-value": TEXT_VALUE,
        "spinner": "bold #ff9f1c",
        "input-area": "bg:#151a23 #e6edf3",
        "text-area": "bg:#151a23 #e6edf3",
        "text-area.prompt": "bold #ff9f1c",
    }
)

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _brand_fragments() -> list[tuple[str, str]]:
    return [
        ("class:chrome", "╭─ "),
        ("class:brand-badge", " AKVAN "),
        ("class:brand-version", f" v{__version__}"),
    ]


def _screen_header(
    title: str,
    subtitle: str,
    *,
    step: tuple[int, int] | None = None,
) -> list[tuple[str, str]]:
    fragments = _brand_fragments()
    fragments.append(("class:title", f"  {title}\n"))
    fragments.append(("class:chrome", "│  "))
    if step is not None:
        current, total = step
        fragments.append(("class:step", f"Step {current} of {total}  ·  "))
    fragments.append(("class:subtitle", subtitle))
    return fragments


def _footer_hint(text: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = [("class:chrome", "╰─ ")]
    for index, part in enumerate(text.split("  ·  ")):
        if index:
            fragments.append(("class:hint", "  ·  "))
        if " " in part:
            key, rest = part.split(" ", 1)
            fragments.append(("class:hint-key", key))
            fragments.append(("class:hint", f" {rest}"))
        else:
            fragments.append(("class:hint-key", part))
    return fragments


SELECTOR_SEPARATOR = "__separator__"


def _is_selector_separator(item: tuple[str, str]) -> bool:
    return item[0] == SELECTOR_SEPARATOR


def _selectable_indices(items: list[tuple[str, str]]) -> list[int]:
    return [index for index, item in enumerate(items) if not _is_selector_separator(item)]


def _format_item_fragments(label: str, *, selected: bool) -> list[tuple[str, str]]:
    lines = label.split("\n", 1)
    style = "class:selected" if selected else "class:item"
    if selected:
        fragments: list[tuple[str, str]] = [
            ("class:selected-marker", "  ❯ "),
            (style, lines[0]),
        ]
    else:
        fragments = [("class:chrome", "    "), (style, lines[0])]
    if len(lines) > 1:
        detail_style = "class:selected" if selected else "class:item-muted"
        fragments.append(("", "\n"))
        fragments.append(("class:chrome", "      "))
        fragments.append((detail_style, lines[1].lstrip()))
    return fragments


def _format_kv_message(pairs: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in pairs)


def configured_providers(settings: Settings) -> set[str]:
    configured: set[str] = set()
    if settings.openrouter_api_key:
        configured.add("openrouter")
    if settings.openai_api_key:
        configured.add("openai-codex")
    elif settings.codex_auth_mode == "cli":
        try:
            auth_path = (
                None
                if not settings.codex_cli_auth_path
                else Path(settings.codex_cli_auth_path)
            )
            load_codex_cli_token(auth_path)
            configured.add("openai-codex")
        except ProviderError:
            pass
    if settings.deepseek_api_key:
        configured.add("deepseek")
    if settings.akvan_api_key:
        configured.add("akvan")
    return configured


def needs_provider_setup(settings: Settings) -> bool:
    return not configured_providers(settings)


def _provider_label(name: str, *, configured: bool, current: bool) -> str:
    mark = "✓ " if configured else "  "
    suffix = "  · current" if current else ""
    return f"{mark}{name}{suffix}"


def run_full_screen_selector(
    *,
    title: str,
    subtitle: str,
    items: list[tuple[str, str]],
    default: str | None = None,
    step: tuple[int, int] | None = None,
) -> str | None:
    if not items:
        return None
    selectable = _selectable_indices(items)
    default_index = next(
        (index for index, (value, _) in enumerate(items) if value == default),
        selectable[0] if selectable else 0,
    )
    selected = default_index if default_index in selectable else (selectable[0] if selectable else 0)
    bindings = KeyBindings()
    query = ""

    def visible_items() -> list[tuple[str, str]]:
        if not query:
            return items
        needle = query.lower()
        return [
            item for item in items
            if not _is_selector_separator(item)
            and (needle in item[0].lower() or needle in item[1].lower())
        ]

    def move(event, amount: int) -> None:
        nonlocal selected
        visible = visible_items()
        selectable_visible = _selectable_indices(visible)
        if not selectable_visible:
            selected = 0
        else:
            try:
                position = selectable_visible.index(selected)
            except ValueError:
                position = 0
            position = min(max(0, position + amount), len(selectable_visible) - 1)
            selected = selectable_visible[position]
        event.app.invalidate()

    @bindings.add("up")
    def _(event) -> None:
        move(event, -1)

    @bindings.add("down")
    def _(event) -> None:
        move(event, 1)

    @bindings.add("pageup")
    def _(event) -> None:
        move(event, -10)

    @bindings.add("pagedown")
    def _(event) -> None:
        move(event, 10)

    @bindings.add("home")
    def _(event) -> None:
        nonlocal selected
        selected = 0
        event.app.invalidate()

    @bindings.add("end")
    def _(event) -> None:
        nonlocal selected
        selectable_visible = _selectable_indices(visible_items())
        selected = selectable_visible[-1] if selectable_visible else 0
        event.app.invalidate()

    @bindings.add("enter")
    def _(event) -> None:
        visible = visible_items()
        if visible and not _is_selector_separator(visible[selected]):
            event.app.exit(result=visible[selected][0])

    @bindings.add("backspace")
    def _(event) -> None:
        nonlocal query, selected
        query = query[:-1]
        selected = 0
        event.app.invalidate()

    @bindings.add("c-u")
    def _(event) -> None:
        nonlocal query, selected
        query = ""
        selected = 0
        event.app.invalidate()

    @bindings.add(Keys.Any)
    def _(event) -> None:
        nonlocal query, selected
        if event.data and event.data.isprintable():
            query += event.data
            selected = 0
            event.app.invalidate()

    @bindings.add("escape")
    def _(event) -> None:
        nonlocal query, selected
        if query:
            query = ""
            selected = 0
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @bindings.add("c-c")
    def _(event) -> None:
        event.app.exit(result=None)

    def item_fragments():
        fragments = []
        visible = visible_items()
        if not visible:
            return [("class:subtitle", "  No matching items")]
        for index, (value, label) in enumerate(visible):
            if _is_selector_separator((value, label)):
                if index < len(visible) - 1:
                    fragments.append(("class:separator", "    ───────────────\n"))
                continue
            if index == selected:
                fragments.append(("[SetCursorPosition]", ""))
            fragments.extend(_format_item_fragments(label, selected=index == selected))
            if index < len(visible) - 1:
                fragments.append(("", "\n"))
        return fragments

    def header_fragments():
        search = query or "type to filter"
        return _screen_header(title, subtitle, step=step) + [
            ("class:chrome", "\n│  "),
            ("class:search-label", "Search "),
            ("class:search-value", search),
        ]

    list_control = FormattedTextControl(
        item_fragments,
        focusable=True,
        show_cursor=False,
    )
    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(header_fragments),
                    height=6,
                ),
                Window(char="─", style="class:line", height=1),
                Window(list_control, wrap_lines=False),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(
                        _footer_hint(
                            "↑/↓ move  ·  type to filter  ·  Enter select  ·  Esc clear/cancel"
                        )
                    ),
                    height=2,
                ),
            ],
            style="class:setup",
        ),
        focused_element=list_control,
    )
    return Application(
        layout=layout,
        key_bindings=bindings,
        style=SETUP_STYLE,
        full_screen=True,
        mouse_support=False,
    ).run()


def run_full_screen_input(
    *,
    title: str,
    prompt: str,
    default: str = "",
    password: bool = False,
    step: tuple[int, int] | None = None,
) -> str | None:
    bindings = KeyBindings()
    field = TextArea(
        text=default,
        multiline=False,
        password=password,
        prompt="❯ ",
        style="class:input-area",
    )

    @bindings.add("enter")
    def _(event) -> None:
        event.app.exit(result=field.text)

    @bindings.add("escape")
    @bindings.add("c-c")
    def _(event) -> None:
        event.app.exit(result=None)

    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(
                        _screen_header(title, prompt, step=step),
                    ),
                    height=5,
                    wrap_lines=True,
                ),
                Window(char="─", style="class:line", height=1),
                field,
                Window(),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(
                        _footer_hint("Enter continue  ·  Esc cancel")
                    ),
                    height=2,
                ),
            ],
            style="class:setup",
        ),
        focused_element=field,
    )
    return Application(
        layout=layout,
        key_bindings=bindings,
        style=SETUP_STYLE,
        full_screen=True,
        mouse_support=False,
    ).run()


def run_full_screen_message(*, title: str, text: str) -> None:
    bindings = KeyBindings()

    @bindings.add("enter")
    @bindings.add("escape")
    @bindings.add("c-c")
    def _(event) -> None:
        event.app.exit()

    body_fragments = _brand_fragments() + [
        ("class:title", f"\n  {title}\n\n"),
    ]
    for line in text.splitlines() or [""]:
        body_fragments.append(("class:item", f"  {line}\n"))

    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(body_fragments),
                    wrap_lines=True,
                ),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(_footer_hint("Enter close")),
                    height=2,
                ),
            ],
            style="class:setup",
        )
    )
    Application(
        layout=layout,
        key_bindings=bindings,
        style=SETUP_STYLE,
        full_screen=True,
        mouse_support=False,
    ).run()


def run_full_screen_task(*, title: str, text: str, callback):
    result: dict[str, object] = {}
    started = time.monotonic()

    def body_fragments():
        elapsed = time.monotonic() - started
        frame = _SPINNER_FRAMES[int(elapsed * 10) % len(_SPINNER_FRAMES)]
        return _screen_header(title, text) + [
            ("class:spinner", f"\n\n  {frame} Working…"),
        ]

    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(body_fragments),
                    wrap_lines=True,
                ),
            ],
            style="class:setup",
        )
    )
    app = Application(
        layout=layout,
        style=SETUP_STYLE,
        full_screen=True,
        mouse_support=False,
        refresh_interval=0.1,
    )

    def start_work() -> None:
        def work() -> None:
            try:
                result["value"] = callback()
            except Exception as exc:
                result["error"] = exc
            app.loop.call_soon_threadsafe(app.exit)

        threading.Thread(target=work, daemon=True).start()

    app.run(pre_run=start_work)
    error = result.get("error")
    if isinstance(error, Exception):
        raise error
    return result.get("value")


def _restart_running_gateways_after_model_change() -> None:
    results = restart_running_gateways()
    if not results:
        return
    lines = "\n".join(f"{gateway_id}: {message}" for gateway_id, _, message in results)
    run_full_screen_message(
        title="Gateways restarted",
        text=(
            "Running gateways were restarted to apply the new provider and model.\n\n"
            f"{lines}"
        ),
    )


def _can_run_interactive_setup() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_model_setup(console: Console) -> int:
    if not _can_run_interactive_setup():
        console.print(
            "[red]Model setup needs an interactive terminal.[/red]\n"
            "Run `akvan model` directly from a terminal, or set "
            "AKVAN_SKIP_SETUP=1 when installing in automation."
        )
        return 1

    current = load_setup_settings()
    provider_id = select_provider(
        console, current.provider, configured_providers(current)
    )
    if provider_id is None:
        return 1

    configurator = PROVIDER_CONFIGURATORS.get(provider_id)
    if configurator is None:
        run_full_screen_message(
            title="Provider unavailable",
            text=f"Provider {provider_id} is not implemented.",
        )
        return 2
    result = configurator(console, current)
    if result == 0:
        _restart_running_gateways_after_model_change()
    return result


def select_provider(
    console: Console,
    current_provider: str,
    configured_providers: set[str],
) -> str | None:
    items: list[tuple[str, str]] = []
    for provider in PROVIDER_OPTIONS:
        label = _provider_label(
            provider["name"],
            configured=provider["id"] in configured_providers,
            current=provider["id"] == current_provider,
        )
        items.append((provider["id"], label))

    default = current_provider if any(
        value == current_provider for value, _ in items
    ) else items[0][0]
    return run_full_screen_selector(
        title="Provider",
        subtitle="",
        items=items,
        default=default,
        step=(1, 3),
    )


def configure_openrouter(console: Console, current) -> int:
    prompt = (
        "Enter a new key, or press Enter to keep the configured key."
        if current.openrouter_api_key
        else "Create a key at https://openrouter.ai/settings/keys, then enter it."
    )
    entered_key = run_full_screen_input(
        title="OpenRouter API key",
        prompt=prompt,
        default=current.openrouter_api_key,
        password=True,
        step=(2, 3),
    )
    if entered_key is None:
        return 1
    api_key = entered_key.strip() or current.openrouter_api_key
    if not api_key:
        run_full_screen_message(
            title="API key required",
            text="OpenRouter requires an API key before models can be loaded.",
        )
        return 2

    provider = OpenRouterProvider(api_key)

    def load_models():
        try:
            return provider.list_models()
        finally:
            provider.close()

    try:
        models = run_full_screen_task(
            title="OpenRouter",
            text="Loading the live model catalog",
            callback=load_models,
        )
    except ProviderError as exc:
        run_full_screen_message(
            title="Could not load models",
            text=str(exc),
        )
        return 2

    model = select_model(console, models, current.model)
    if model is None:
        return 1

    env_path = save_settings(
        provider="openrouter",
        model=model,
        openrouter_api_key=api_key,
    )
    run_full_screen_message(
        title="Configuration saved",
        text=_format_kv_message([
            ("Provider", "openrouter"),
            ("Model", model),
            ("File", str(env_path)),
        ]),
    )
    return 0


def configure_openai_codex(console: Console, current) -> int:
    auth_mode = run_full_screen_selector(
        title="OpenAI Codex authentication",
        subtitle="Choose how Akvan should authenticate to OpenAI Codex",
        items=[
            (
                "cli",
                "Codex CLI session\n"
                "Use the existing `codex login` session",
            ),
            (
                "api-key",
                "OpenAI API key\n"
                "Store OPENAI_API_KEY in ~/.akvan/.env",
            ),
        ],
        default=current.codex_auth_mode if current.codex_auth_mode in {"cli", "api-key"} else "cli",
        step=(2, 3),
    )
    if auth_mode is None:
        return 1

    openai_api_key = current.openai_api_key
    codex_auth_path = current.codex_cli_auth_path
    if auth_mode == "api-key":
        prompt = (
            "Enter a new key, or press Enter to keep the configured key."
            if current.openai_api_key
            else "Enter your OpenAI API key."
        )
        entered_key = run_full_screen_input(
            title="OpenAI API key",
            prompt=prompt,
            default=current.openai_api_key,
            password=True,
            step=(2, 3),
        )
        if entered_key is None:
            return 1
        openai_api_key = entered_key.strip() or current.openai_api_key
        if not openai_api_key:
            run_full_screen_message(
                title="API key required",
                text="OpenAI Codex API-key mode requires OPENAI_API_KEY.",
            )
            return 2
    else:
        codex_auth_path = ""
        try:
            load_codex_cli_token()
        except ProviderError as exc:
            run_full_screen_message(
                title="Codex CLI session not ready",
                text=(
                    f"{exc}\n\n"
                    "Akvan checks Codex session files in this order: "
                    "$CODEX_HOME/auth.json, ~/.codex/auth.json, then ~/codex/auth.json. "
                    "Run `codex login` to create that session, or choose OpenAI API key mode."
                ),
            )
            return 2

    if auth_mode == "cli":
        credential = load_codex_cli_token()
    else:
        credential = openai_api_key
    provider = OpenAICodexProvider(credential, auth_mode=auth_mode)

    def load_models():
        try:
            return provider.list_models()
        except ProviderError:
            return list(DEFAULT_CODEX_MODELS)
        finally:
            provider.close()

    try:
        models = run_full_screen_task(
            title="OpenAI Codex",
            text="Loading the Codex model catalog",
            callback=load_models,
        )
    except ProviderError as exc:
        run_full_screen_message(
            title="Could not load models",
            text=str(exc),
        )
        return 2

    if not models:
        models = list(DEFAULT_CODEX_MODELS)

    model = select_model(
        console,
        models,
        current.model if current.provider == "openai-codex" else DEFAULT_CODEX_MODEL,
        title="OpenAI Codex models",
        step=(3, 3),
    )
    if model is None:
        return 1

    env_path = save_settings(
        provider="openai-codex",
        model=model,
        openrouter_api_key=current.openrouter_api_key,
        openai_api_key=openai_api_key,
        codex_auth_mode=auth_mode,
        codex_cli_auth_path=codex_auth_path if auth_mode == "cli" else "",
    )
    run_full_screen_message(
        title="Configuration saved",
        text=_format_kv_message([
            ("Provider", "openai-codex"),
            ("Auth", auth_mode),
            ("Model", model),
            ("File", str(env_path)),
        ]),
    )
    return 0


def configure_deepseek(console: Console, current) -> int:
    prompt = (
        "Enter a new key, or press Enter to keep the configured key."
        if current.deepseek_api_key
        else "Create a key at https://platform.deepseek.com/api_keys, then enter it."
    )
    entered_key = run_full_screen_input(
        title="DeepSeek API key",
        prompt=prompt,
        default=current.deepseek_api_key,
        password=True,
        step=(2, 3),
    )
    if entered_key is None:
        return 1
    api_key = entered_key.strip() or current.deepseek_api_key
    if not api_key:
        run_full_screen_message(
            title="API key required",
            text="DeepSeek requires an API key before models can be loaded.",
        )
        return 2

    provider = DeepSeekProvider(api_key)

    def load_models():
        try:
            return provider.list_models()
        except ProviderError:
            return list(DEFAULT_DEEPSEEK_MODELS)
        finally:
            provider.close()

    try:
        models = run_full_screen_task(
            title="DeepSeek",
            text="Loading the DeepSeek model catalog",
            callback=load_models,
        )
    except ProviderError as exc:
        run_full_screen_message(
            title="Could not load models",
            text=str(exc),
        )
        return 2

    default_model = (
        current.model
        if current.provider == "deepseek"
        else DEFAULT_DEEPSEEK_MODEL
    )
    model = select_model(
        console,
        models,
        default_model,
        title="DeepSeek models",
        step=(3, 3),
    )
    if model is None:
        return 1

    env_path = save_settings(
        provider="deepseek",
        model=model,
        openrouter_api_key=current.openrouter_api_key,
        deepseek_api_key=api_key,
    )
    run_full_screen_message(
        title="Configuration saved",
        text=_format_kv_message([
            ("Provider", "deepseek"),
            ("Model", model),
            ("File", str(env_path)),
        ]),
    )
    return 0


def _agent_auth_request(
  backend_url: str,
  path: str,
  payload: dict,
) -> dict:
    url = f"{backend_url.rstrip('/')}{path}"
    try:
        response = httpx.post(url, json=payload, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = "Request failed"
        try:
            data = exc.response.json()
            if isinstance(data, dict):
                detail = str(data.get("detail") or data.get("message") or detail)
        except ValueError:
            detail = exc.response.text or detail
        raise ProviderError(detail) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach Akvan backend: {exc}") from exc
    data = response.json()
    if not isinstance(data, dict):
        raise ProviderError("Akvan backend returned an unexpected response.")
    return data


def configure_akvan(console: Console, current) -> int:
    backend_url = DEFAULT_AKVAN_BACKEND_URL

    identifier = run_full_screen_input(
        title="Akvan sign in",
        prompt="Enter your email or phone number to receive an OTP.",
        default="",
        step=(2, 3),
    )
    if identifier is None:
        return 1
    identifier = identifier.strip()
    if not identifier:
        run_full_screen_message(
            title="Identifier required",
            text="Enter the email or phone number linked to your Akvan account.",
        )
        return 2

    def send_otp():
        return _agent_auth_request(
            backend_url,
            "/api/agent/auth/send-otp/",
            {"email_or_phone": identifier},
        )

    try:
        run_full_screen_task(
            title="Akvan",
            text="Sending OTP",
            callback=send_otp,
        )
    except ProviderError as exc:
        run_full_screen_message(title="Could not send OTP", text=str(exc))
        return 2

    otp = run_full_screen_input(
        title="Akvan OTP",
        prompt="Enter the 6-digit code sent to your email or phone.",
        default="",
        step=(3, 3),
    )
    if otp is None:
        return 1
    otp = otp.strip()
    if not otp:
        run_full_screen_message(title="OTP required", text="Enter the verification code.")
        return 2

    def verify_otp():
        return _agent_auth_request(
            backend_url,
            "/api/agent/auth/verify-otp/",
            {"email_or_phone": identifier, "otp": otp},
        )

    try:
        auth_data = run_full_screen_task(
            title="Akvan",
            text="Verifying OTP",
            callback=verify_otp,
        )
    except ProviderError as exc:
        run_full_screen_message(title="Could not verify OTP", text=str(exc))
        return 2

    api_key = str(auth_data.get("api_key") or "").strip()
    if not api_key:
        run_full_screen_message(
            title="Sign in failed",
            text="Akvan did not return an API key.",
        )
        return 2

    account = auth_data.get("account") if isinstance(auth_data.get("account"), dict) else {}
    links = auth_data.get("links") if isinstance(auth_data.get("links"), dict) else {}
    summary_lines = [
        ("Account", str(account.get("identifier") or identifier)),
        ("Plan", str(account.get("plan_name") or account.get("plan_code") or "unknown")),
        ("Credits", str(account.get("remaining_credits") or "0")),
    ]
    if links.get("plans"):
        summary_lines.append(("Plans", str(links["plans"])))
    if links.get("credits"):
        summary_lines.append(("Top up", str(links["credits"])))
    run_full_screen_message(
        title="Signed in to Akvan",
        text=_format_kv_message(summary_lines),
    )

    provider = AkvanProvider(api_key, backend_url=backend_url)

    def load_models():
        try:
            return provider.list_models()
        finally:
            provider.close()

    try:
        models = run_full_screen_task(
            title="Akvan",
            text="Loading models available on your plan",
            callback=load_models,
        )
    except ProviderError as exc:
        run_full_screen_message(
            title="Could not load models",
            text=str(exc),
        )
        return 2

    default_model = (
        current.model if current.provider == "akvan" else models[0].id
    )
    model = select_model(
        console,
        models,
        default_model,
        title="Akvan models",
        step=(3, 3),
    )
    if model is None:
        return 1

    env_path = save_settings(
        provider="akvan",
        model=model,
        akvan_api_key=api_key,
        akvan_backend_url=backend_url,
    )
    run_full_screen_message(
        title="Configuration saved",
        text=_format_kv_message([
            ("Provider", "akvan"),
            ("Model", model),
            ("File", str(env_path)),
        ]),
    )
    return 0


PROVIDER_CONFIGURATORS = {
    "openrouter": configure_openrouter,
    "openai-codex": configure_openai_codex,
    "deepseek": configure_deepseek,
    "akvan": configure_akvan,
}


def select_model(
    console: Console,
    models: list[ModelInfo],
    current_model: str,
    *,
    page_size: int = 15,
    title: str = "Model",
    step: tuple[int, int] | None = (3, 3),
) -> str | None:
    if not models:
        return None
    available_ids = {model.id for model in models}
    default_model = (
        current_model if current_model in available_ids else models[0].id
    )
    ordered = sorted(models, key=lambda model: model.id != default_model)
    items: list[tuple[str, str]] = []
    for model in ordered:
        context = f"{model.context_length:,}" if model.context_length else "—"
        current = " · current" if model.id == default_model else ""
        label = (
            f"{model.id}\n"
            f"{context} tokens  ·  {model.name}{current}"
        )
        items.append((model.id, label))

    return run_full_screen_selector(
        title=title,
        subtitle=f"{len(items)} models available",
        items=items,
        default=default_model,
        step=step,
    )


