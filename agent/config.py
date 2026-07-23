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
DEFAULT_AKVAN_MODEL = "openai/gpt-4o-mini"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_CODEX_AUTH_MODE = "cli"
DEFAULT_AKVAN_BACKEND_URL = "https://agent.akvan.app"
SUPPORTED_PROVIDERS = {"openrouter", "openai-codex", "deepseek", "akvan"}
SUPPORTED_CODEX_AUTH_MODES = {"api-key", "cli"}
SUPPORTED_DEEPSEEK_THINKING_MODES = {"enabled", "disabled"}
SUPPORTED_DEEPSEEK_REASONING_EFFORTS = {"low", "medium", "high", "max"}
SUPPORTED_VISION_MODES = {"auto", "native", "aux", "off"}


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
    web_extract_summary_model: str = ""
    vision_mode: str = "auto"
    aux_vision_model: str = ""
    model_supports_vision: bool | None = None
    akvan_api_key: str = ""
    akvan_backend_url: str = DEFAULT_AKVAN_BACKEND_URL


def save_settings(
    *,
    provider: str,
    model: str,
    openrouter_api_key: str = "",
    openai_api_key: str = "",
    codex_auth_mode: str = DEFAULT_CODEX_AUTH_MODE,
    codex_cli_auth_path: str = "",
    deepseek_api_key: str = "",
    akvan_api_key: str = "",
    akvan_backend_url: str = DEFAULT_AKVAN_BACKEND_URL,
    project_root: Path | None = None,
) -> Path:
    from agent.storage.permissions import (
        ensure_private_file,
        harden_akvan_home,
        is_under_akvan_home,
    )

    root = project_root or akvan_home()
    env_path = root / ".env"
    if is_under_akvan_home(root):
        harden_akvan_home(root)
    else:
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
    if akvan_api_key:
        set_key(str(env_path), "AKVAN_API_KEY", akvan_api_key, quote_mode="never")
    if akvan_backend_url:
        set_key(
            str(env_path),
            "AKVAN_BACKEND_URL",
            akvan_backend_url,
            quote_mode="never",
        )
    ensure_private_file(env_path)
    return env_path


def _env_value(dotenv: dict[str, str | None], key: str, default: str = "") -> str:
    return os.getenv(key, dotenv.get(key) or default).strip()


def _env_bool_optional(
    dotenv: dict[str, str | None], key: str
) -> bool | None:
    raw = _env_value(dotenv, key)
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


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
    elif provider == "akvan":
        model_default = DEFAULT_AKVAN_MODEL
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
        web_extract_summary_model=_env_value(dotenv, "AKVAN_WEB_EXTRACT_SUMMARY_MODEL"),
        vision_mode=_env_value(dotenv, "AKVAN_VISION_MODE", "auto").lower() or "auto",
        aux_vision_model=_env_value(dotenv, "AKVAN_AUX_VISION_MODEL"),
        model_supports_vision=_env_bool_optional(dotenv, "AKVAN_MODEL_SUPPORTS_VISION"),
        akvan_api_key=_env_value(dotenv, "AKVAN_API_KEY"),
        akvan_backend_url=_env_value(
            dotenv, "AKVAN_BACKEND_URL", DEFAULT_AKVAN_BACKEND_URL
        )
        or DEFAULT_AKVAN_BACKEND_URL,
    )


def resolve_enabled_toolsets(
    base: tuple[str, ...] | None = None,
    *,
    project_root: Path | None = None,
) -> tuple[str, ...]:
    """Return default toolsets, appending web/memory/sessions when configured."""

    from agent.memory.config import is_memory_enabled
    from agent.knowledge.config import is_knowledge_enabled
    from agent.tools.browser_runtime.config import (
        is_banner_generation_configured,
        is_browser_configured,
    )
    from agent.tools.web.config import is_web_configured
    from agent.tools.telegram_delivery import is_telegram_delivery_configured

    toolsets = list(base or ("core", "files", "terminal", "skills"))
    if is_memory_enabled(project_root=project_root):
        for name in ("memory", "sessions"):
            if name not in toolsets:
                toolsets.append(name)
    if is_knowledge_enabled(project_root=project_root) and "knowledge" not in toolsets:
        toolsets.append("knowledge")
    if is_web_configured(project_root=project_root) and "web" not in toolsets:
        toolsets.append("web")
    if (
        is_banner_generation_configured(project_root=project_root)
        and "banner_generation" not in toolsets
    ):
        toolsets.append("banner_generation")
    if is_browser_configured(project_root=project_root) and "browser" not in toolsets:
        toolsets.append("browser")
    if (
        is_telegram_delivery_configured(project_root=project_root)
        and "telegram_delivery" not in toolsets
    ):
        toolsets.append("telegram_delivery")
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
    akvan_api_key = current.akvan_api_key
    akvan_backend_url = current.akvan_backend_url

    if current.approval_mode not in {"ask", "deny", "off"}:
        raise ValueError("AKVAN_APPROVAL_MODE must be one of: ask, deny, off")
    if current.approval_timeout < 1:
        raise ValueError("AKVAN_APPROVAL_TIMEOUT must be at least 1")
    if not 1 <= current.terminal_timeout <= 600:
        raise ValueError("AKVAN_TERMINAL_TIMEOUT must be between 1 and 600")
    if current.vision_mode not in SUPPORTED_VISION_MODES:
        raise ValueError(
            "AKVAN_VISION_MODE must be one of: "
            + ", ".join(sorted(SUPPORTED_VISION_MODES))
        )

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

    if provider == "akvan":
        if not akvan_api_key and prompt_for_missing_key:
            akvan_api_key = getpass("Akvan API key: ").strip()
        if not akvan_api_key:
            raise ValueError(
                "AKVAN_API_KEY is required. Run `akvan model` to sign in with OTP."
            )
        if not akvan_backend_url:
            raise ValueError("AKVAN_BACKEND_URL is required for the Akvan provider.")

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
        web_extract_summary_model=current.web_extract_summary_model,
        vision_mode=current.vision_mode,
        aux_vision_model=current.aux_vision_model,
        model_supports_vision=current.model_supports_vision,
        akvan_api_key=akvan_api_key,
        akvan_backend_url=akvan_backend_url,
    )
