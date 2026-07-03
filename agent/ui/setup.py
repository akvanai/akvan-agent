"""Provider and model setup wizard."""

from __future__ import annotations

import threading

from rich.console import Console
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.styles import Style

from agent.config import DEFAULT_CODEX_MODEL, DEFAULT_DEEPSEEK_MODEL, load_setup_settings, save_settings
from agent.ui.rendering import PROMPT_INPUT_BG
from agent.providers.base import ModelInfo, ProviderError
from agent.providers.deepseek import DEFAULT_DEEPSEEK_MODELS, DeepSeekProvider
from agent.providers.openai_codex import DEFAULT_CODEX_MODELS, load_codex_cli_token
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
)


SETUP_STYLE = Style.from_dict(
    {
        "setup": "",
        "title": "bold #00ffff",
        "subtitle": "#ffaa00",
        "selected": "bg:#0000ff #ffffff bold",
        "item": "",
        "line": "#8b00ff",
        "hint": "#ffff00",
        "input-area": f"bg:{PROMPT_INPUT_BG}",
        "text-area": f"bg:{PROMPT_INPUT_BG} #e8e8e8",
        "text-area.prompt": "#00ffff bold",
    }
)


def run_full_screen_selector(
    *,
    title: str,
    subtitle: str,
    items: list[tuple[str, str]],
    default: str | None = None,
) -> str | None:
    if not items:
        return None
    selected = next(
        (index for index, (value, _) in enumerate(items) if value == default),
        0,
    )
    bindings = KeyBindings()
    query = ""

    def visible_items() -> list[tuple[str, str]]:
        if not query:
            return items
        needle = query.lower()
        return [
            item for item in items
            if needle in item[0].lower() or needle in item[1].lower()
        ]

    def move(event, amount: int) -> None:
        nonlocal selected
        visible = visible_items()
        if not visible:
            selected = 0
        else:
            selected = min(max(0, selected + amount), len(visible) - 1)
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
        selected = max(0, len(visible_items()) - 1)
        event.app.invalidate()

    @bindings.add("enter")
    def _(event) -> None:
        visible = visible_items()
        if visible:
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
        for index, (_, label) in enumerate(visible):
            style = "class:selected" if index == selected else "class:item"
            prefix = "❯ " if index == selected else "  "
            if index == selected:
                fragments.append(("[SetCursorPosition]", ""))
            fragments.append((style, prefix + label))
            if index < len(visible) - 1:
                fragments.append(("", "\n"))
        return fragments

    def header_fragments():
        search = query or "type to filter"
        return [
            ("class:title", f"  {title}\n"),
            ("class:subtitle", f"  {subtitle}\n"),
            ("class:hint", f"  Search: {search}"),
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
                    height=4,
                ),
                Window(char="─", style="class:line", height=1),
                Window(list_control, wrap_lines=False),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(
                        [
                            (
                                "class:hint",
                                "  ↑/↓ move  ·  type to filter  ·  Enter select  ·  Esc clear/cancel",
                            )
                        ]
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
                        [
                            ("class:title", f"  {title}\n"),
                            ("class:subtitle", f"  {prompt}"),
                        ]
                    ),
                    height=4,
                ),
                Window(char="─", style="class:line", height=1),
                field,
                Window(),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(
                        [("class:hint", "  Enter continue  ·  Esc cancel")]
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

    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(
                        [
                            ("class:title", f"  {title}\n\n"),
                            ("class:item", f"  {text}"),
                        ]
                    ),
                    wrap_lines=True,
                ),
                Window(char="─", style="class:line", height=1),
                Window(
                    FormattedTextControl(
                        [("class:hint", "  Enter close")]
                    ),
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
    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(
                        [
                            ("class:title", f"  {title}\n\n"),
                            ("class:subtitle", f"  {text}\n\n"),
                            ("class:hint", "  Please wait..."),
                        ]
                    ),
                    wrap_lines=True,
                )
            ],
            style="class:setup",
        )
    )
    app = Application(
        layout=layout,
        style=SETUP_STYLE,
        full_screen=True,
        mouse_support=False,
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


def run_model_setup(console: Console) -> int:
    current = load_setup_settings()
    configured = set()
    if current.openrouter_api_key:
        configured.add("openrouter")
    if current.openai_api_key or current.codex_auth_mode == "cli":
        configured.add("openai-codex")
    provider_id = select_provider(console, current.provider, configured)
    if provider_id is None:
        return 1

    configurator = PROVIDER_CONFIGURATORS.get(provider_id)
    if configurator is None:
        run_full_screen_message(
            title="Provider unavailable",
            text=f"Provider {provider_id} is not implemented.",
        )
        return 2
    return configurator(console, current)


def select_provider(
    console: Console,
    current_provider: str,
    configured_providers: set[str],
) -> str | None:
    items: list[tuple[str, str]] = []
    for provider in PROVIDER_OPTIONS:
        configured = provider["id"] in configured_providers
        status = "configured" if configured else "needs setup"
        if provider["id"] == current_provider:
            status += " · current"
        name = provider["name"]
        description = provider["description"]
        label = f"{name:<18} {status:<22} {description}"
        items.append((provider["id"], label))

    default = current_provider if any(
        value == current_provider for value, _ in items
    ) else items[0][0]
    return run_full_screen_selector(
        title="AKVAN · PROVIDER",
        subtitle="Choose the provider to configure",
        items=items,
        default=default,
    )


def configure_openrouter(console: Console, current) -> int:
    prompt = (
        "Enter a new key, or press Enter to keep the configured key."
        if current.openrouter_api_key
        else "Create a key at https://openrouter.ai/settings/keys, then enter it."
    )
    entered_key = run_full_screen_input(
        title="AKVAN · OPENROUTER KEY",
        prompt=prompt,
        default=current.openrouter_api_key,
        password=True,
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
            title="AKVAN · OPENROUTER",
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
        title="CONFIGURATION SAVED",
        text=(
            f"Provider  openrouter\n"
            f"Model     {model}\n"
            f"File      {env_path}"
        ),
    )
    return 0


def configure_openai_codex(console: Console, current) -> int:
    auth_mode = run_full_screen_selector(
        title="AKVAN · OPENAI CODEX AUTH",
        subtitle="Choose how Akvan should authenticate to OpenAI Codex",
        items=[
            ("cli", "Codex CLI session       Use the existing `codex login` session"),
            ("api-key", "OpenAI API key          Store OPENAI_API_KEY in this project .env"),
        ],
        default=current.codex_auth_mode if current.codex_auth_mode in {"cli", "api-key"} else "cli",
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
            title="AKVAN · OPENAI KEY",
            prompt=prompt,
            default=current.openai_api_key,
            password=True,
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

    model = select_model(
        console,
        list(DEFAULT_CODEX_MODELS),
        current.model if current.provider == "openai-codex" else DEFAULT_CODEX_MODEL,
        title="AKVAN · OPENAI CODEX MODELS",
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
        title="CONFIGURATION SAVED",
        text=(
            f"Provider  openai-codex\n"
            f"Auth      {auth_mode}\n"
            f"Model     {model}\n"
            f"File      {env_path}"
        ),
    )
    return 0


def configure_deepseek(console: Console, current) -> int:
    prompt = (
        "Enter a new key, or press Enter to keep the configured key."
        if current.deepseek_api_key
        else "Create a key at https://platform.deepseek.com/api_keys, then enter it."
    )
    entered_key = run_full_screen_input(
        title="AKVAN · DEEPSEEK KEY",
        prompt=prompt,
        default=current.deepseek_api_key,
        password=True,
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
            title="AKVAN · DEEPSEEK",
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
        title="AKVAN · DEEPSEEK MODELS",
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
        title="CONFIGURATION SAVED",
        text=(
            f"Provider  deepseek\n"
            f"Model     {model}\n"
            f"File      {env_path}"
        ),
    )
    return 0


PROVIDER_CONFIGURATORS = {
    "openrouter": configure_openrouter,
    "openai-codex": configure_openai_codex,
    "deepseek": configure_deepseek,
}


def select_model(
    console: Console,
    models: list[ModelInfo],
    current_model: str,
    *,
    page_size: int = 15,
    title: str = "AKVAN · OPENROUTER MODELS",
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
        current = "  current" if model.id == default_model else ""
        label = f"{model.id:<48} {context:>12}  {model.name}{current}"
        items.append((model.id, label))

    return run_full_screen_selector(
        title=title,
        subtitle=(
            f"{len(items)} models  ·  model ID  ·  context  ·  display name"
        ),
        items=items,
        default=default_model,
    )


