"""
Handles configuration for providers, credentials, and model choices.
Loads shell variables and project .env values in a predictable order.
Saves interactive setup changes back to .env with restricted permissions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from dotenv import dotenv_values, set_key

DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_CODEX_AUTH_MODE = "cli"
SUPPORTED_PROVIDERS = {"openrouter", "openai-codex", "deepseek"}
SUPPORTED_CODEX_AUTH_MODES = {"api-key", "cli"}
SUPPORTED_DEEPSEEK_THINKING_MODES = {"enabled", "disabled"}
SUPPORTED_DEEPSEEK_REASONING_EFFORTS = {"low", "medium", "high", "max"}


def akvan_home() -> Path:
    """Return the user-level Akvan state and configuration directory."""

    configured = os.getenv("AKVAN_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".akvan"


def state_db_path() -> Path:
    """Return the SQLite session database path under the Akvan home directory."""

    return akvan_home() / "state.db"


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    openrouter_api_key: str = ""
    approval_mode: str = "ask"
    approval_timeout: int = 60
    terminal_timeout: int = 120
    openai_api_key: str = ""
    codex_auth_mode: str = DEFAULT_CODEX_AUTH_MODE
    codex_cli_auth_path: str = ""
    deepseek_api_key: str = ""
    deepseek_thinking: str = "enabled"
    deepseek_reasoning_effort: str = ""
    deepseek_base_url: str = ""
    web_search_backend: str = ""
    web_extract_backend: str = ""
    searxng_url: str = ""
    firecrawl_api_url: str = ""
    firecrawl_api_key: str = ""
    web_extract_summary_model: str = ""


def save_settings(
    *,
    provider: str,
    model: str,
    openrouter_api_key: str = "",
    openai_api_key: str = "",
    codex_auth_mode: str = DEFAULT_CODEX_AUTH_MODE,
    codex_cli_auth_path: str = "",
    deepseek_api_key: str = "",
    project_root: Path | None = None,
) -> Path:
    root = project_root or akvan_home()
    env_path = root / ".env"
    root.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch(mode=0o600)
    set_key(str(env_path), "AKVAN_PROVIDER", provider, quote_mode="never")
    set_key(str(env_path), "AKVAN_MODEL", model, quote_mode="never")
    if openrouter_api_key:
        set_key(
            str(env_path),
            "OPENROUTER_API_KEY",
            openrouter_api_key,
            quote_mode="never",
        )
    if provider == "openai-codex":
        set_key(
            str(env_path),
            "AKVAN_CODEX_AUTH_MODE",
            codex_auth_mode,
            quote_mode="never",
        )
        if openai_api_key:
            set_key(str(env_path), "OPENAI_API_KEY", openai_api_key, quote_mode="never")
        if codex_cli_auth_path:
            set_key(
                str(env_path),
                "AKVAN_CODEX_AUTH_PATH",
                codex_cli_auth_path,
                quote_mode="never",
            )
    if deepseek_api_key:
        set_key(
            str(env_path),
            "DEEPSEEK_API_KEY",
            deepseek_api_key,
            quote_mode="never",
        )
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    return env_path


def _env_value(dotenv: dict[str, str | None], key: str, default: str = "") -> str:
    return os.getenv(key, dotenv.get(key) or default).strip()


def load_setup_settings(*, project_root: Path | None = None) -> Settings:
    global_root = akvan_home()
    root = project_root or Path.cwd()
    dotenv = dict(dotenv_values(global_root / ".env"))
    if root.resolve() != global_root.resolve():
        dotenv.update(dotenv_values(root / ".env"))
    provider = _env_value(dotenv, "AKVAN_PROVIDER", "openrouter").lower()
    if provider == "openai-codex":
        model_default = DEFAULT_CODEX_MODEL
    elif provider == "deepseek":
        model_default = DEFAULT_DEEPSEEK_MODEL
    else:
        model_default = DEFAULT_MODEL
    return Settings(
        provider=provider,
        model=_env_value(dotenv, "AKVAN_MODEL", model_default) or model_default,
        openrouter_api_key=_env_value(dotenv, "OPENROUTER_API_KEY"),
        openai_api_key=_env_value(dotenv, "OPENAI_API_KEY"),
        codex_auth_mode=_env_value(
            dotenv, "AKVAN_CODEX_AUTH_MODE", DEFAULT_CODEX_AUTH_MODE
        ).lower()
        or DEFAULT_CODEX_AUTH_MODE,
        codex_cli_auth_path=_env_value(dotenv, "AKVAN_CODEX_AUTH_PATH"),
        deepseek_api_key=_env_value(dotenv, "DEEPSEEK_API_KEY"),
        deepseek_thinking=_env_value(dotenv, "AKVAN_DEEPSEEK_THINKING", "enabled").lower()
        or "enabled",
        deepseek_reasoning_effort=_env_value(dotenv, "AKVAN_DEEPSEEK_REASONING_EFFORT"),
        deepseek_base_url=_env_value(dotenv, "DEEPSEEK_BASE_URL"),
        approval_mode=_env_value(dotenv, "AKVAN_APPROVAL_MODE", "ask").lower(),
        approval_timeout=int(_env_value(dotenv, "AKVAN_APPROVAL_TIMEOUT", "60")),
        terminal_timeout=int(_env_value(dotenv, "AKVAN_TERMINAL_TIMEOUT", "120")),
        web_search_backend=_env_value(dotenv, "AKVAN_WEB_SEARCH_BACKEND"),
        web_extract_backend=_env_value(dotenv, "AKVAN_WEB_EXTRACT_BACKEND"),
        searxng_url=_env_value(dotenv, "SEARXNG_URL"),
        firecrawl_api_url=_env_value(dotenv, "FIRECRAWL_API_URL"),
        firecrawl_api_key=_env_value(dotenv, "FIRECRAWL_API_KEY"),
        web_extract_summary_model=_env_value(dotenv, "AKVAN_WEB_EXTRACT_SUMMARY_MODEL"),
    )


def resolve_enabled_toolsets(
    base: tuple[str, ...] | None = None,
    *,
    project_root: Path | None = None,
) -> tuple[str, ...]:
    """Return default toolsets, appending ``web`` when configured."""

    from agent.tools.web.config import is_web_configured

    toolsets = list(base or ("core", "files", "terminal", "skills"))
    if is_web_configured(project_root=project_root) and "web" not in toolsets:
        toolsets.append("web")
    return tuple(toolsets)


def load_settings(
    *,
    project_root: Path | None = None,
    prompt_for_missing_key: bool = True,
) -> Settings:
    current = load_setup_settings(project_root=project_root)
    provider = current.provider
    model = current.model
    openrouter_api_key = current.openrouter_api_key
    openai_api_key = current.openai_api_key
    codex_auth_mode = current.codex_auth_mode
    deepseek_api_key = current.deepseek_api_key
    deepseek_thinking = current.deepseek_thinking
    deepseek_reasoning_effort = current.deepseek_reasoning_effort
    deepseek_base_url = current.deepseek_base_url

    if current.approval_mode not in {"ask", "deny", "off"}:
        raise ValueError("AKVAN_APPROVAL_MODE must be one of: ask, deny, off")
    if current.approval_timeout < 1:
        raise ValueError("AKVAN_APPROVAL_TIMEOUT must be at least 1")
    if not 1 <= current.terminal_timeout <= 600:
        raise ValueError("AKVAN_TERMINAL_TIMEOUT must be between 1 and 600")

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported provider '{provider}'. Supported providers: "
            + ", ".join(sorted(SUPPORTED_PROVIDERS))
        )

    if provider == "openrouter":
        if not openrouter_api_key and prompt_for_missing_key:
            openrouter_api_key = getpass("OpenRouter API key: ").strip()
        if not openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required. Set it in your environment or project .env file."
            )

    if provider == "openai-codex":
        if codex_auth_mode not in SUPPORTED_CODEX_AUTH_MODES:
            raise ValueError(
                "AKVAN_CODEX_AUTH_MODE must be one of: "
                + ", ".join(sorted(SUPPORTED_CODEX_AUTH_MODES))
            )
        if codex_auth_mode == "api-key":
            if not openai_api_key and prompt_for_missing_key:
                openai_api_key = getpass("OpenAI API key: ").strip()
            if not openai_api_key:
                raise ValueError(
                    "OPENAI_API_KEY is required for OpenAI Codex API-key mode. "
                    "Set it in your environment or project .env file, or use AKVAN_CODEX_AUTH_MODE=cli."
                )

    if provider == "deepseek":
        if deepseek_thinking not in SUPPORTED_DEEPSEEK_THINKING_MODES:
            raise ValueError(
                "AKVAN_DEEPSEEK_THINKING must be one of: "
                + ", ".join(sorted(SUPPORTED_DEEPSEEK_THINKING_MODES))
            )
        if deepseek_reasoning_effort and deepseek_reasoning_effort not in (
            SUPPORTED_DEEPSEEK_REASONING_EFFORTS
        ):
            raise ValueError(
                "AKVAN_DEEPSEEK_REASONING_EFFORT must be one of: "
                + ", ".join(sorted(SUPPORTED_DEEPSEEK_REASONING_EFFORTS))
            )
        if not deepseek_api_key and prompt_for_missing_key:
            deepseek_api_key = getpass("DeepSeek API key: ").strip()
        if not deepseek_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required. Set it in your environment or project .env file."
            )

    return Settings(
        provider=provider,
        model=model,
        openrouter_api_key=openrouter_api_key,
        openai_api_key=openai_api_key,
        codex_auth_mode=codex_auth_mode,
        codex_cli_auth_path=current.codex_cli_auth_path,
        deepseek_api_key=deepseek_api_key,
        deepseek_thinking=deepseek_thinking,
        deepseek_reasoning_effort=deepseek_reasoning_effort,
        deepseek_base_url=deepseek_base_url,
        approval_mode=current.approval_mode,
        approval_timeout=current.approval_timeout,
        terminal_timeout=current.terminal_timeout,
        web_search_backend=current.web_search_backend,
        web_extract_backend=current.web_extract_backend,
        searxng_url=current.searxng_url,
        firecrawl_api_url=current.firecrawl_api_url,
        firecrawl_api_key=current.firecrawl_api_key,
        web_extract_summary_model=current.web_extract_summary_model,
    )
