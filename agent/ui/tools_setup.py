"""Optional tools setup wizard."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from agent.tools.browser_runtime.docker import DockerRuntimeError, ensure_docker_runtime, remove_docker_runtime
from agent.tools.browser_runtime.config import (
    DEFAULT_RUNTIME_HOST,
    DEFAULT_RUNTIME_MODE,
    DEFAULT_RUNTIME_PORT,
    banner_generation_config,
    browser_config,
    browser_runtime_config,
    is_banner_generation_configured,
    is_browser_configured,
    is_browser_runtime_configured,
    is_docker_browser_runtime,
    profiles_dir,
    save_browser_tools_yaml,
)
from agent.tools.browser_runtime.profiles import (
    InteractiveLoginSession,
    ProfileError,
    delete_profile,
    display_available,
    ensure_profiles_ready,
    import_storage_state,
    list_profiles,
    migrate_legacy_x_auth,
    profile_status,
    validate_profile_name,
)
from agent.tools.web.config import (
    get_extract_backend,
    get_search_backend,
    env_path,
    is_extract_configured,
    is_search_configured,
    load_web_yaml,
    save_web_env,
    save_web_yaml,
    searxng_runtime_config,
    web_env_values,
)
from agent.tools.web.searxng_runtime import (
    DEFAULT_SEARXNG_HOST,
    DEFAULT_SEARXNG_PORT,
    SearXNGRuntimeError,
    ensure_searxng_runtime,
    has_matching_searxng_runtime,
    is_port_free,
    remove_searxng_runtime,
    suggest_next_port,
)
from agent.tools.web.searxng_runtime.config import is_managed_searxng
from agent.tools.web.verify import verify_ddgs_available, verify_searxng_url
from agent.tools.telegram_delivery.config import (
    has_explicit_telegram_delivery_settings,
    has_telegram_gateway_credentials,
    is_telegram_delivery_configured,
    save_telegram_delivery_settings,
    telegram_delivery_credentials_csv,
    gateway_telegram_credentials_csv,
)
from agent.ui.setup import (
    SELECTOR_SEPARATOR,
    run_full_screen_input,
    run_full_screen_message,
    run_full_screen_selector,
    run_full_screen_task,
)
from agent.ui.telegram_setup import prompt_telegram_bot_credentials


def _menu_with_footer(
    items: list[tuple[str, str]],
    *footer: tuple[str, str],
) -> list[tuple[str, str]]:
    if not footer:
        return items
    return [*items, (SELECTOR_SEPARATOR, ""), *footer]


def _docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def _with_privilege(cmd: list[str]) -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    sudo = shutil.which("sudo")
    return [sudo, *cmd] if sudo else cmd


def _docker_install_commands() -> list[list[str]]:
    if sys.platform != "linux":
        return []
    if shutil.which("apt-get"):
        return [
            _with_privilege(["apt-get", "update"]),
            _with_privilege(["apt-get", "install", "-y", "docker.io"]),
        ]
    if shutil.which("dnf"):
        return [_with_privilege(["dnf", "install", "-y", "docker"])]
    if shutil.which("yum"):
        return [_with_privilege(["yum", "install", "-y", "docker"])]
    if shutil.which("pacman"):
        return [_with_privilege(["pacman", "-Sy", "--noconfirm", "docker"])]
    return []


def _format_commands(commands: list[list[str]]) -> str:
    return "; ".join(" ".join(command) for command in commands)


def _install_docker(commands: list[list[str]]) -> str:
    for command in commands:
        subprocess.check_call(command)
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run(
            _with_privilege([systemctl, "enable", "--now", "docker"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    if not _docker_is_available():
        raise RuntimeError(
            "Docker was installed, but the Docker daemon is not available yet. "
            "Start Docker and try again."
        )
    return "Docker is installed and running."


def _ensure_docker_available_with_prompt(*, title: str) -> bool:
    if _docker_is_available():
        return True
    commands = _docker_install_commands()
    if not commands:
        run_full_screen_message(
            title="Docker not available",
            text=(
                "Docker is required for this managed runtime, but Akvan could not "
                "find a supported automatic installer on this system. Install Docker "
                "manually, start the Docker daemon, and try again."
            ),
        )
        return False
    choice = run_full_screen_selector(
        title="Docker not available",
        subtitle="Install Docker before continuing?",
        items=[
            ("install", f"Install Docker  run: {_format_commands(commands)}"),
            ("cancel", "Cancel  return without installing Docker"),
        ],
        default="install",
    )
    if choice != "install":
        return False
    try:
        run_full_screen_task(
            title=title,
            text="Installing Docker",
            callback=lambda: _install_docker(commands),
        )
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        run_full_screen_message(
            title="Docker install failed",
            text=str(exc),
        )
        return False
    return True


def _search_status() -> str:
    if is_search_configured():
        return "configured"
    return "needs setup"


def _extract_status() -> str:
    if is_extract_configured():
        return "active"
    return "inactive"


def _default_search_provider_name() -> str:
    from agent.tools.web.config import is_backend_available
    from agent.tools.web.registry import get_provider

    backend = get_search_backend()
    if backend:
        return backend
    for candidate in ("ddgs", "searxng"):
        provider = get_provider(candidate)
        if provider is not None and is_backend_available(candidate):
            return candidate
    return "ddgs"


def _main_menu_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            (
                "search",
                f"Search provider  {_search_status()}  SearXNG or DuckDuckGo (ddgs)",
            ),
            (
                "extract",
                f"Extract provider  {_extract_status()}  built-in HTML extractor",
            ),
        ],
        ("back", "Back  return to tools menu"),
    )


def _search_provider_items() -> list[tuple[str, str]]:
    from agent.tools.web.registry import search_providers

    items = []
    for provider in search_providers():
        schema = provider.get_setup_schema()
        status = "configured" if provider.is_available() else "needs setup"
        items.append(
            (
                provider.name,
                f"{schema.get('name', provider.display_name)}  {status}  "
                f"{schema.get('tag', '')}",
            )
        )
    items.append(("back", "Back  return to web tools menu"))
    return _menu_with_footer(items[:-1], items[-1])


def _teardown_managed_searxng_if_active(*, show_progress: bool = True) -> None:
    if not is_managed_searxng():
        return
    try:
        if show_progress:
            run_full_screen_task(
                title="SearXNG",
                text="Stopping managed SearXNG container",
                callback=remove_searxng_runtime,
            )
        else:
            remove_searxng_runtime()
    except SearXNGRuntimeError:
        return


def _searxng_mode_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            (
                "managed",
                "Deploy local SearXNG with Akvan  recommended  Docker, no Redis",
            ),
            (
                "existing",
                "I already have SearXNG  enter instance URL",
            ),
        ],
        ("back", "Back  return to search provider menu"),
    )


def _configure_searxng_existing(*, provider_display_name: str) -> tuple[int, str]:
    current = web_env_values().get("SEARXNG_URL", "")
    default = current or "http://localhost:8080"
    entered = run_full_screen_input(
        title="SearXNG",
        prompt="SearXNG instance URL",
        default=default,
    )
    if entered is None:
        return 1, ""
    value = entered.strip() or default
    if not value:
        run_full_screen_message(
            title="Value required",
            text=f"SEARXNG_URL is required for {provider_display_name}.",
        )
        return 2, ""
    try:
        run_full_screen_task(
            title="SearXNG",
            text="Verifying SearXNG connectivity",
            callback=lambda: verify_searxng_url(value),
        )
    except ValueError as exc:
        run_full_screen_message(
            title="Could not verify SearXNG",
            text=str(exc),
        )
        return 2, ""
    return 0, value


def _configure_searxng_managed_port() -> tuple[int, int]:
    runtime_cfg = searxng_runtime_config()
    suggested_port = runtime_cfg["port"] if runtime_cfg["mode"] == "managed" else DEFAULT_SEARXNG_PORT
    host = DEFAULT_SEARXNG_HOST

    while True:
        entered = run_full_screen_input(
            title="SearXNG",
            prompt=f"Local port on {host}",
            default=str(suggested_port),
        )
        if entered is None:
            return 1, 0
        text = entered.strip() or str(suggested_port)
        try:
            port = int(text)
        except ValueError:
            run_full_screen_message(title="Invalid port", text="Port must be a number.")
            continue
        if port < 1 or port > 65535:
            run_full_screen_message(
                title="Invalid port",
                text="Port must be between 1 and 65535.",
            )
            continue
        if not is_port_free(host, port) and not has_matching_searxng_runtime(host=host, port=port):
            suggested_port = suggest_next_port(host, port + 1)
            run_full_screen_message(
                title="Port in use",
                text=(
                    f"Port {port} is already in use on {host}.\n"
                    f"Try another port such as {suggested_port}."
                ),
            )
            continue
        return 0, port


def _configure_searxng() -> tuple[int, str, dict[str, object]]:
    mode = run_full_screen_selector(
        title="SearXNG",
        subtitle="Choose how to connect SearXNG",
        items=_searxng_mode_items(),
        default="managed",
    )
    if mode is None or mode == "back":
        return 1, "", {}

    if mode == "existing":
        _teardown_managed_searxng_if_active()
        code, url = _configure_searxng_existing(provider_display_name="SearXNG")
        if code:
            return code, "", {}
        return 0, url, {"mode": "external"}

    result = _configure_searxng_managed_port()
    if result[0]:
        return result[0], "", {}
    port = result[1]
    host = DEFAULT_SEARXNG_HOST
    if not _ensure_docker_available_with_prompt(title="SearXNG"):
        return 1, "", {}

    try:
        base_url = run_full_screen_task(
            title="SearXNG",
            text="Starting local SearXNG container",
            callback=lambda: ensure_searxng_runtime(port=port, host=host),
        )
        run_full_screen_task(
            title="SearXNG",
            text="Verifying SearXNG connectivity",
            callback=lambda: verify_searxng_url(base_url, wait_seconds=60, timeout=10),
        )
    except (SearXNGRuntimeError, ValueError) as exc:
        run_full_screen_message(
            title="Managed SearXNG failed",
            text=str(exc),
        )
        return 2, "", {}

    return 0, base_url, {"mode": "managed", "port": port, "host": host}


def _configure_search_provider(provider_name: str) -> int:
    from agent.tools.web.registry import get_provider

    provider = get_provider(provider_name)
    if provider is None:
        run_full_screen_message(
            title="Provider unavailable",
            text=f"Unknown search provider: {provider_name}",
        )
        return 2

    schema = provider.get_setup_schema()
    env_values: dict[str, str] = {"AKVAN_WEB_SEARCH_BACKEND": provider_name}
    searxng_yaml: dict[str, object] | None = None
    clear_searxng = False

    if provider_name == "ddgs":
        _teardown_managed_searxng_if_active()
        clear_searxng = bool(load_web_yaml().get("searxng"))
        try:
            run_full_screen_task(
                title="DuckDuckGo",
                text="Checking ddgs package availability",
                callback=verify_ddgs_available,
            )
        except ValueError as exc:
            install = run_full_screen_selector(
                title="Install ddgs",
                subtitle="The ddgs package is required for DuckDuckGo search",
                items=[
                    ("install", "Install now  pip install ddgs"),
                    ("cancel", "Cancel  return without saving"),
                ],
                default="install",
            )
            if install != "install":
                return 1
            try:
                run_full_screen_task(
                    title="Install ddgs",
                    text="Installing ddgs via pip",
                    callback=lambda: subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", "ddgs"],
                    ),
                )
                verify_ddgs_available()
            except (subprocess.CalledProcessError, ValueError) as exc:
                run_full_screen_message(
                    title="Install failed",
                    text=str(exc),
                )
                return 2
    elif provider_name == "searxng":
        code, searxng_url, searxng_yaml = _configure_searxng()
        if code:
            return code
        env_values["SEARXNG_URL"] = searxng_url
    else:
        for field in schema.get("env_vars", []):
            key = field["key"]
            current = web_env_values().get(key, "")
            default = field.get("default", current)
            entered = run_full_screen_input(
                title=schema.get("name", provider.display_name),
                prompt=field.get("prompt", key),
                default=default,
                password=bool(field.get("secret")),
            )
            if entered is None:
                return 1
            value = entered.strip() or default
            if not value:
                run_full_screen_message(
                    title="Value required",
                    text=f"{key} is required for {provider.display_name}.",
                )
                return 2
            env_values[key] = value

    env_file = save_web_env(env_values)
    yaml_file = save_web_yaml(
        search_backend=provider_name,
        searxng=searxng_yaml,
        clear_searxng=clear_searxng,
    )
    run_full_screen_message(
        title="Configuration saved",
        text=(
            f"Search backend  {provider_name}\n"
            f"Env file        {env_file}\n"
            f"Config file     {yaml_file}"
        ),
    )
    return 0


def _configure_extract_provider() -> int:
    if is_extract_configured():
        run_full_screen_message(
            title="Extract provider",
            text=(
                "Built-in HTML extraction is active by default.\n"
                f"Backend  {get_extract_backend()}\n"
                f"Env file {env_path()}"
            ),
        )
        return 0
    env_file = save_web_env({"AKVAN_WEB_EXTRACT_BACKEND": "content_extractor"})
    yaml_file = save_web_yaml(extract_backend="content_extractor")
    run_full_screen_message(
        title="Configuration saved",
        text=(
            "Extract backend content_extractor\n"
            "Built-in HTML extraction is enabled.\n"
            f"Env file        {env_file}\n"
            f"Config file     {yaml_file}"
        ),
    )
    return 0


def _run_web_tools_setup() -> int:
    while True:
        choice = run_full_screen_selector(
            title="Web tools",
            subtitle="Configure web search and page extraction",
            items=_main_menu_items(),
            default="search",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "search":
            provider_choice = run_full_screen_selector(
                title="Search provider",
                subtitle="Pick a backend for web_search",
                items=_search_provider_items(),
                default=_default_search_provider_name(),
            )
            if provider_choice is None or provider_choice == "back":
                continue
            result = _configure_search_provider(provider_choice)
            if result:
                return result
        elif choice == "extract":
            result = _configure_extract_provider()
            if result:
                return result
    return 0


def _teardown_docker_browser_runtime_if_active(*, show_progress: bool = True) -> None:
    if not is_docker_browser_runtime():
        return
    try:
        if show_progress:
            run_full_screen_task(
                title="Browser runtime",
                text="Stopping Docker browser runtime container",
                callback=remove_docker_runtime,
            )
        else:
            remove_docker_runtime()
    except DockerRuntimeError:
        return


def _browser_runtime_status() -> str:
    return "active" if is_browser_runtime_configured() else "inactive"


def _browser_status() -> str:
    return "active" if is_browser_configured() else "inactive"


def _banner_status() -> str:
    return "active" if is_banner_generation_configured() else "inactive"


def _telegram_delivery_status() -> str:
    if not is_telegram_delivery_configured():
        return "needs setup"
    if has_explicit_telegram_delivery_settings():
        return "configured"
    return "configured (via gateway)"


def _browser_menu_items() -> list[tuple[str, str]]:
    profiles = list_profiles()
    ready = sum(1 for item in profiles if item.get("ready"))
    return _menu_with_footer(
        [
            (
                "browser",
                (
                    f"Browser  {_browser_status()}  "
                    "navigate, snapshot, click with auth profiles"
                ),
            ),
            (
                "profiles",
                f"Auth profiles  {ready}/{len(profiles)} ready  import or interactive login",
            ),
            (
                "runtime",
                (
                    f"Runtime  {_browser_runtime_status()}  "
                    "how Chromium runs (shared with banners)"
                ),
            ),
        ],
        ("back", "Back  return to tools menu"),
    )


def _social_menu_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            (
                "telegram_delivery",
                (
                    f"Telegram delivery  {_telegram_delivery_status()}  "
                    "send files and text via Telegram bot"
                ),
            ),
        ],
        ("back", "Back  return to tools menu"),
    )


def _art_menu_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            (
                "banner",
                f"Banner generation  {_banner_status()}  templates and image rendering",
            ),
        ],
        ("back", "Back  return to tools menu"),
    )


def _browser_runtime_mode_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            ("local", "Local Playwright  host Python/Node Playwright install"),
            (
                "docker",
                "Docker container  recommended  Akvan-managed runtime container",
            ),
        ],
        ("back", "Back  return to previous screen"),
    )


def _browser_runtime_mode_default() -> str:
    mode = str(browser_runtime_config().get("mode") or DEFAULT_RUNTIME_MODE).lower().strip()
    if mode in {"local", "docker"}:
        return mode
    return "docker"


def _configure_browser_runtime() -> int:
    run_full_screen_message(
        title="Browser runtime requirements",
        text=(
            "Browser tools use Chromium through Playwright.\n"
            "Recommended: 2 CPU cores, 2 GB free RAM, and 1 GB free disk.\n"
            "Docker mode is recommended: one shared container managed by Akvan. "
            "Local mode uses the host Playwright install."
        ),
    )
    mode = run_full_screen_selector(
        title="Browser runtime",
        subtitle="Choose how browser-based tools should run",
        items=_browser_runtime_mode_items(),
        default=_browser_runtime_mode_default(),
    )
    if mode == "back":
        return 0
    if mode is None:
        return 1
    port = run_full_screen_input(
        title="Browser runtime",
        prompt="Runtime port",
        default=str(browser_runtime_config().get("port") or DEFAULT_RUNTIME_PORT),
    )
    if port is None:
        return 1
    try:
        port_number = int(port)
    except ValueError:
        run_full_screen_message(title="Invalid port", text="Port must be a number.")
        return 2
    if mode == "local":
        _teardown_docker_browser_runtime_if_active()
    if mode == "docker" and not _ensure_docker_available_with_prompt(title="Docker browser runtime"):
        return 1
    path = save_browser_tools_yaml(
        browser_runtime={
            "enabled": True,
            "mode": mode,
            "host": DEFAULT_RUNTIME_HOST,
            "port": port_number,
        }
    )
    if mode == "docker":
        try:
            run_full_screen_task(
                title="Docker browser runtime",
                text="Starting Akvan browser runtime container",
                callback=lambda: ensure_docker_runtime(
                    config=browser_runtime_config(),
                ),
            )
        except DockerRuntimeError as exc:
            run_full_screen_message(
                title="Docker runtime failed",
                text=str(exc),
            )
            return 2
    run_full_screen_message(
        title="Browser runtime saved",
        text=f"Mode {mode}\nURL http://{DEFAULT_RUNTIME_HOST}:{port_number}\nConfig file {path}",
    )
    return 0


def _configure_banner_generation() -> int:
    if not is_browser_runtime_configured():
        result = _configure_browser_runtime()
        if result == 2 or not is_browser_runtime_configured():
            return result
    current = banner_generation_config()
    root_dir = run_full_screen_input(
        title="Banner generation",
        prompt="Akvan-managed banner workspace",
        default=str(current["root_dir"]),
    )
    if root_dir is None:
        return 1
    default_size = run_full_screen_selector(
        title="Banner generation",
        subtitle="Default banner size",
        items=[
            ("x_landscape", "X landscape  1200x675"),
            ("square", "Square  1080x1080"),
            ("story", "Story  1080x1920"),
        ],
        default=str(current["default_size"]),
    )
    if default_size is None:
        return 1
    path = save_browser_tools_yaml(
        banner_generation={
            "enabled": True,
            "root_dir": root_dir.strip(),
            "default_template": str(current["default_template"]),
            "default_size": default_size,
        }
    )
    from agent.tools.banner_generation import ensure_banner_workspace

    ensure_banner_workspace()
    run_full_screen_message(
        title="Banner generation saved",
        text=(
            "Banner generation is enabled. Templates, renders, and assets are "
            "kept together in the managed banner workspace.\n"
            f"Config file {path}"
        ),
    )
    return 0


def _configure_telegram_delivery() -> int:
    current_token = ""
    current_users = ""
    if has_explicit_telegram_delivery_settings():
        current_token, current_users = telegram_delivery_credentials_csv()

    other_token = ""
    other_users = ""
    other_name = None
    if has_telegram_gateway_credentials():
        other_token, other_users = gateway_telegram_credentials_csv()
        other_name = "Telegram gateway"

    credentials = prompt_telegram_bot_credentials(
        title="Telegram delivery",
        other_side_name=other_name,
        other_token=other_token,
        other_allowed_users=other_users,
        current_token=current_token,
        current_allowed_users=current_users,
    )
    if credentials is None:
        return 1
    bot_token, allowed_users = credentials
    path = save_telegram_delivery_settings(
        bot_token=bot_token,
        allowed_users=allowed_users,
    )
    run_full_screen_message(
        title="Telegram delivery saved",
        text=(
            "Telegram image delivery is enabled for authorized users.\n"
            f"File  {path}\n\n"
            "Ask the recipient to open the bot and send /start before the first send."
        ),
    )
    return 0


def _configure_browser() -> int:
    if not is_browser_runtime_configured():
        result = _configure_browser_runtime()
        if result == 2 or not is_browser_runtime_configured():
            return result
    ensure_profiles_ready()
    migrated = migrate_legacy_x_auth()
    current = browser_config()
    choice = run_full_screen_selector(
        title="Browser",
        subtitle="Enable agent browser tools with optional auth profiles",
        items=_menu_with_footer(
            [
                ("enable", "Enable  register browser_* tools"),
                ("disable", "Disable  hide browser_* tools (runtime can stay on for banners)"),
            ],
            ("back", "Back"),
        ),
        default="enable" if current["enabled"] else "disable",
    )
    if choice is None or choice == "back":
        return 0 if choice == "back" else 1
    enabled = choice == "enable"
    path = save_browser_tools_yaml(
        browser={
            "enabled": enabled,
            "inactivity_timeout_seconds": int(current["inactivity_timeout_seconds"]),
            "profiles_dir": str(profiles_dir()),
        }
    )
    note = ""
    if migrated:
        note = f"\nMigrated legacy X auth into profile {migrated['name']!r}."
    run_full_screen_message(
        title="Browser saved",
        text=(
            f"Browser is {'enabled' if enabled else 'disabled'}.\n"
            f"Config file  {path}"
            f"{note}\n\n"
            "Add auth profiles under Browser → Auth profiles. "
            "On VPS/headless hosts, import a Playwright storage state file."
        ),
    )
    return 0


def _profiles_menu_items() -> list[tuple[str, str]]:
    ensure_profiles_ready()
    items: list[tuple[str, str]] = [
        ("add", "Add profile  import file or interactive login"),
        ("list", "List profiles  show ready status"),
    ]
    for profile in list_profiles():
        name = str(profile["name"])
        ready = "ready" if profile.get("ready") else "missing"
        items.append((f"manage:{name}", f"{name}  {ready}  re-import or delete"))
    return _menu_with_footer(items, ("back", "Back"))


def _add_auth_profile() -> int:
    ensure_profiles_ready()
    name = run_full_screen_input(
        title="Add auth profile",
        prompt="Profile name (e.g. x, github)",
        default="",
    )
    if name is None:
        return 1
    try:
        profile = validate_profile_name(name)
    except ProfileError as exc:
        run_full_screen_message(title="Invalid name", text=str(exc))
        return 2

    method_items = [
        ("import", "Import storage state file  recommended for VPS and X"),
    ]
    if display_available():
        method_items.append(
            ("login", "Interactive login  local GUI only (headed Chromium)")
        )
    else:
        method_items.append(
            (
                "login_unavailable",
                "Interactive login unavailable  no display (use import / scp from desktop)",
            )
        )
    method = run_full_screen_selector(
        title=f"Profile {profile}",
        subtitle="How do you want to provide auth?",
        items=_menu_with_footer(method_items, ("back", "Back")),
        default="import",
    )
    if method is None or method == "back":
        return 0 if method == "back" else 1
    if method == "login_unavailable":
        run_full_screen_message(
            title="Use import on this host",
            text=(
                "This host looks headless (VPS/SSH).\n"
                "Log in on a desktop browser, export a Playwright storage_state JSON, "
                f"copy it here, then import it as profile {profile!r}."
            ),
        )
        return 0
    if method == "import":
        return _import_auth_profile(profile)
    return _interactive_auth_profile(profile)


def _import_auth_profile(profile: str) -> int:
    path_text = run_full_screen_input(
        title=f"Import profile {profile}",
        prompt="Path to Playwright storage_state JSON",
        default="",
    )
    if path_text is None:
        return 1
    if not path_text.strip():
        run_full_screen_message(title="Path required", text="Provide a storage state file path.")
        return 2
    start_url = run_full_screen_input(
        title=f"Import profile {profile}",
        prompt="Optional start URL hint (e.g. https://x.com)",
        default="",
    )
    if start_url is None:
        return 1
    try:
        result = import_storage_state(
            profile,
            path_text.strip(),
            source="import",
            start_url=start_url.strip() or None,
        )
    except ProfileError as exc:
        run_full_screen_message(title="Import failed", text=str(exc))
        return 2
    run_full_screen_message(
        title="Profile saved",
        text=(
            f"Profile {result['name']!r} is ready.\n"
            f"Managed path  {result['storage_state_path']}\n\n"
            "Enable Browser if you have not already, then ask the agent "
            f"to browser_start(profile={result['name']!r})."
        ),
    )
    return 0


def _interactive_auth_profile(profile: str) -> int:
    start_url = run_full_screen_input(
        title=f"Login for profile {profile}",
        prompt="Start URL (optional)",
        default="https://x.com/login",
    )
    if start_url is None:
        return 1
    session = InteractiveLoginSession(profile, start_url=start_url.strip() or None)
    try:
        session.open()
    except ProfileError as exc:
        run_full_screen_message(title="Login failed", text=str(exc))
        return 2
    run_full_screen_message(
        title="Complete login in the browser",
        text=(
            "A headed Chromium window should be open.\n"
            "Log in to the site, then press Enter here to save the session.\n"
            "Sites like X may block automated browsers — prefer Import in that case."
        ),
    )
    confirm = run_full_screen_selector(
        title=f"Save profile {profile}?",
        subtitle="Save storage state after you finished logging in",
        items=_menu_with_footer(
            [
                ("save", "Save and close browser"),
                ("cancel", "Cancel without saving"),
            ],
            ("back", "Cancel"),
        ),
        default="save",
    )
    if confirm != "save":
        session.close()
        return 0
    try:
        result = session.save_and_close()
    except ProfileError as exc:
        session.close()
        run_full_screen_message(title="Save failed", text=str(exc))
        return 2
    run_full_screen_message(
        title="Profile saved",
        text=f"Profile {result['name']!r} is ready.\nPath  {result['storage_state_path']}",
    )
    return 0


def _manage_auth_profile(profile: str) -> int:
    status = profile_status(profile)
    choice = run_full_screen_selector(
        title=f"Profile {profile}",
        subtitle="ready" if status.get("ready") else "missing storage state",
        items=_menu_with_footer(
            [
                ("reimport", "Replace storage state  import file"),
                ("delete", "Delete profile"),
            ],
            ("back", "Back"),
        ),
        default="reimport",
    )
    if choice is None or choice == "back":
        return 0 if choice == "back" else 1
    if choice == "reimport":
        return _import_auth_profile(profile)
    confirm = run_full_screen_selector(
        title=f"Delete profile {profile}?",
        subtitle="This removes the managed storage state",
        items=_menu_with_footer(
            [("yes", "Yes, delete"), ("no", "No, keep")],
            ("back", "Back"),
        ),
        default="no",
    )
    if confirm != "yes":
        return 0
    delete_profile(profile)
    run_full_screen_message(title="Deleted", text=f"Profile {profile!r} was removed.")
    return 0


def _run_auth_profiles_setup() -> int:
    ensure_profiles_ready()
    migrated = migrate_legacy_x_auth()
    if migrated:
        run_full_screen_message(
            title="Legacy X auth migrated",
            text=(
                f"Found legacy X auth and imported it as profile {migrated['name']!r}.\n"
                f"Path  {migrated['storage_state_path']}"
            ),
        )
    while True:
        choice = run_full_screen_selector(
            title="Auth profiles",
            subtitle=f"Managed under {profiles_dir()}",
            items=_profiles_menu_items(),
            default="add",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "add":
            result = _add_auth_profile()
            if result == 1:
                return result
        elif choice == "list":
            profiles = list_profiles()
            if not profiles:
                text = "No profiles yet. Add one with Import (recommended on VPS)."
            else:
                lines = []
                for item in profiles:
                    source = (item.get("meta") or {}).get("source") or "?"
                    ready = "ready" if item.get("ready") else "missing"
                    lines.append(f"- {item['name']}  {ready}  source={source}")
                text = "\n".join(lines)
            run_full_screen_message(title="Auth profiles", text=text)
        elif choice.startswith("manage:"):
            result = _manage_auth_profile(choice.split(":", 1)[1])
            if result == 1:
                return result


def _run_browser_tools_setup() -> int:
    while True:
        choice = run_full_screen_selector(
            title="Browser",
            subtitle="Enable browser tools, manage auth profiles, and configure runtime",
            items=_browser_menu_items(),
            default="browser",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "browser":
            result = _configure_browser()
            if result:
                return result
        elif choice == "profiles":
            result = _run_auth_profiles_setup()
            if result:
                return result
        elif choice == "runtime":
            result = _configure_browser_runtime()
            if result == 2:
                return result


def _run_social_tools_setup() -> int:
    while True:
        choice = run_full_screen_selector(
            title="Social Media",
            subtitle="Configure social delivery tools",
            items=_social_menu_items(),
            default="telegram_delivery",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "telegram_delivery":
            result = _configure_telegram_delivery()
            if result:
                return result

def _run_art_tools_setup() -> int:
    while True:
        choice = run_full_screen_selector(
            title="Art and Content Creation",
            subtitle="Configure creative tools",
            items=_art_menu_items(),
            default="banner",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "banner":
            result = _configure_banner_generation()
            if result:
                return result


def _tools_category_items() -> list[tuple[str, str]]:
    return _menu_with_footer(
        [
            (
                "web",
                f"🔎 Search Web  search={_search_status()} extract={_extract_status()}",
            ),
            (
                "browser",
                f"🌐 Browser  browser={_browser_status()}",
            ),
            (
                "social",
                f"🐦 Social Media  telegram={_telegram_delivery_status()}",
            ),
            (
                "art",
                f"🎨 Art and Content Creation  banner={_banner_status()}",
            ),
        ],
        ("back", "↩ Back"),
    )


def run_tools_setup(console: Console) -> int:
    _ = console
    while True:
        choice = run_full_screen_selector(
            title="Tools",
            subtitle="Choose a tool category",
            items=_tools_category_items(),
            default="web",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "web":
            result = _run_web_tools_setup()
            if result:
                return result
        elif choice == "browser":
            result = _run_browser_tools_setup()
            if result:
                return result
        elif choice == "social":
            result = _run_social_tools_setup()
            if result:
                return result
        elif choice == "art":
            result = _run_art_tools_setup()
            if result:
                return result
    return 0
