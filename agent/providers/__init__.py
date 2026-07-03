"""
Provides the public imports for built-in model providers.
Keeps provider discovery imports in one predictable location.
Prevents callers from depending on internal module details.
"""

from pathlib import Path

from agent.config import Settings
from agent.providers.base import Provider, ProviderError, ProviderStreamEvent
from agent.providers.deepseek import DEFAULT_DEEPSEEK_BASE_URL, DeepSeekProvider
from agent.providers.openai_codex import OpenAICodexProvider, load_codex_cli_token
from agent.providers.openrouter import OpenRouterProvider


def build_provider(settings: Settings) -> Provider:
    if settings.provider == "openrouter":
        return OpenRouterProvider(settings.openrouter_api_key)
    if settings.provider == "openai-codex":
        if settings.codex_auth_mode == "cli":
            token = load_codex_cli_token(
                None if not settings.codex_cli_auth_path else Path(settings.codex_cli_auth_path)
            )
            return OpenAICodexProvider(token, auth_mode="cli")
        return OpenAICodexProvider(settings.openai_api_key, auth_mode="api-key")
    if settings.provider == "deepseek":
        base_url = settings.deepseek_base_url or DEFAULT_DEEPSEEK_BASE_URL
        reasoning_effort = settings.deepseek_reasoning_effort or None
        return DeepSeekProvider(
            settings.deepseek_api_key,
            base_url=base_url,
            thinking_enabled=settings.deepseek_thinking == "enabled",
            reasoning_effort=reasoning_effort,
        )
    raise ProviderError(f"Unsupported provider {settings.provider!r}.")


__all__ = [
    "DeepSeekProvider",
    "OpenAICodexProvider",
    "OpenRouterProvider",
    "Provider",
    "ProviderError",
    "ProviderStreamEvent",
    "build_provider",
    "load_codex_cli_token",
]
