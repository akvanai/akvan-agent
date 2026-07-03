"""Web tools setup wizard."""

from __future__ import annotations

import subprocess
import sys

from rich.console import Console

from agent.tools.web.config import (
    DEFAULT_FIRECRAWL_URL,
    env_path,
    is_extract_configured,
    is_search_configured,
    save_web_env,
    save_web_yaml,
    web_env_values,
)
from agent.tools.web.registry import search_providers
from agent.tools.web.verify import verify_ddgs_available, verify_firecrawl, verify_searxng_url
from agent.ui.setup import (
    run_full_screen_input,
    run_full_screen_message,
    run_full_screen_selector,
    run_full_screen_task,
)


def _search_status() -> str:
    if is_search_configured():
        return "configured"
    return "needs setup"


def _extract_status() -> str:
    if is_extract_configured():
        return "configured"
    return "needs setup"


def _main_menu_items() -> list[tuple[str, str]]:
    return [
        (
            "search",
            f"Search provider      {_search_status():<14} SearXNG or DuckDuckGo (ddgs)",
        ),
        (
            "extract",
            f"Extract provider     {_extract_status():<14} Firecrawl self-hosted",
        ),
        ("summary", "View status          Show saved web tool configuration"),
        ("back", "Back                 Return to previous screen"),
    ]


def _search_provider_items() -> list[tuple[str, str]]:
    items = []
    for provider in search_providers():
        schema = provider.get_setup_schema()
        status = "configured" if provider.is_available() else "needs setup"
        items.append(
            (
                provider.name,
                f"{schema.get('name', provider.display_name):<22} "
                f"{status:<14} {schema.get('tag', '')}",
            )
        )
    items.append(("back", "Back                 Return to web tools menu"))
    return items


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

    if provider_name == "ddgs":
        try:
            run_full_screen_task(
                title="AKVAN · DUCKDUCKGO",
                text="Checking ddgs package availability",
                callback=verify_ddgs_available,
            )
        except ValueError as exc:
            install = run_full_screen_selector(
                title="AKVAN · INSTALL DDGS",
                subtitle="The ddgs package is required for DuckDuckGo search",
                items=[
                    ("install", "Install now           pip install ddgs"),
                    ("cancel", "Cancel                Return without saving"),
                ],
                default="install",
            )
            if install != "install":
                return 1
            try:
                run_full_screen_task(
                    title="AKVAN · INSTALL DDGS",
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
    else:
        for field in schema.get("env_vars", []):
            key = field["key"]
            current = web_env_values().get(key, "")
            default = field.get("default", current)
            entered = run_full_screen_input(
                title=f"AKVAN · {schema.get('name', provider.display_name).upper()}",
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
            if provider_name == "searxng":
                try:
                    run_full_screen_task(
                        title="AKVAN · SEARXNG",
                        text="Verifying SearXNG connectivity",
                        callback=lambda: verify_searxng_url(value),
                    )
                except ValueError as exc:
                    run_full_screen_message(
                        title="Could not verify SearXNG",
                        text=str(exc),
                    )
                    return 2

    env_file = save_web_env(env_values)
    yaml_file = save_web_yaml(search_backend=provider_name)
    run_full_screen_message(
        title="CONFIGURATION SAVED",
        text=(
            f"Search backend  {provider_name}\n"
            f"Env file        {env_file}\n"
            f"Config file     {yaml_file}"
        ),
    )
    return 0


def _configure_extract_provider() -> int:
    current = web_env_values()
    api_url = run_full_screen_input(
        title="AKVAN · FIRECRAWL URL",
        prompt="Firecrawl instance URL (self-hosted)",
        default=current.get("FIRECRAWL_API_URL") or DEFAULT_FIRECRAWL_URL,
    )
    if api_url is None:
        return 1
    api_url = api_url.strip() or DEFAULT_FIRECRAWL_URL
    api_key = run_full_screen_input(
        title="AKVAN · FIRECRAWL KEY",
        prompt="Firecrawl API key (optional for self-hosted with auth disabled)",
        default=current.get("FIRECRAWL_API_KEY", ""),
        password=True,
    )
    if api_key is None:
        return 1
    try:
        run_full_screen_task(
            title="AKVAN · FIRECRAWL",
            text="Verifying Firecrawl connectivity",
            callback=lambda: verify_firecrawl(api_url, api_key or ""),
        )
    except ValueError as exc:
        run_full_screen_message(
            title="Could not verify Firecrawl",
            text=str(exc),
        )
        return 2

    env_values = {
        "AKVAN_WEB_EXTRACT_BACKEND": "firecrawl",
        "FIRECRAWL_API_URL": api_url,
    }
    if api_key.strip():
        env_values["FIRECRAWL_API_KEY"] = api_key.strip()
    env_file = save_web_env(env_values)
    yaml_file = save_web_yaml(extract_backend="firecrawl")
    run_full_screen_message(
        title="CONFIGURATION SAVED",
        text=(
            "Extract backend firecrawl\n"
            f"URL             {api_url}\n"
            f"Env file        {env_file}\n"
            f"Config file     {yaml_file}"
        ),
    )
    return 0


def show_web_tools_summary() -> int:
    values = web_env_values()
    rows = [
        f"Search   {_search_status()}  backend={values.get('AKVAN_WEB_SEARCH_BACKEND') or '—'}",
        f"Extract  {_extract_status()}  backend={values.get('AKVAN_WEB_EXTRACT_BACKEND') or '—'}",
        "",
        f"SEARXNG_URL        {values.get('SEARXNG_URL') or '—'}",
        f"FIRECRAWL_API_URL  {values.get('FIRECRAWL_API_URL') or '—'}",
        "",
        f"Env file           {env_path()}",
    ]
    run_full_screen_message(
        title="WEB TOOLS STATUS",
        text="\n".join(rows) + "\n\nManage with: akvan tools",
    )
    return 0


def run_tools_setup(console: Console) -> int:
    _ = console
    while True:
        choice = run_full_screen_selector(
            title="AKVAN · WEB TOOLS",
            subtitle="Configure web search and page extraction",
            items=_main_menu_items(),
            default="search",
        )
        if choice is None or choice == "back":
            return 0
        if choice == "summary":
            show_web_tools_summary()
            continue
        if choice == "search":
            provider_choice = run_full_screen_selector(
                title="AKVAN · SEARCH PROVIDER",
                subtitle="Pick a backend for web_search",
                items=_search_provider_items(),
                default=search_providers()[0].name if search_providers() else "searxng",
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
