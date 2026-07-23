"""Shared configuration and client helpers for browser-based tools."""

from agent.tools.browser_runtime.config import (
    BANNER_SIZE_PRESETS,
    CONTAINER_PROFILES_DIR,
    DEFAULT_RUNTIME_HOST,
    DEFAULT_RUNTIME_MODE,
    DEFAULT_RUNTIME_PORT,
    browser_config,
    browser_runtime_config,
    banner_generation_config,
    is_banner_generation_configured,
    is_browser_configured,
    is_browser_runtime_configured,
    profiles_dir,
)

__all__ = [
    "BANNER_SIZE_PRESETS",
    "CONTAINER_PROFILES_DIR",
    "DEFAULT_RUNTIME_HOST",
    "DEFAULT_RUNTIME_MODE",
    "DEFAULT_RUNTIME_PORT",
    "browser_config",
    "browser_runtime_config",
    "banner_generation_config",
    "is_banner_generation_configured",
    "is_browser_configured",
    "is_browser_runtime_configured",
    "profiles_dir",
]
