"""Configuration loading and persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MODEL,
    load_settings,
    save_agent_settings,
    save_settings,
)


@pytest.fixture(autouse=True)
def isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home" / ".akvan"))
    for key in (
        "AKVAN_MAX_ITERATIONS",
        "AKVAN_YOLO",
        "AKVAN_APPROVAL_MODE",
        "AKVAN_TERMINAL_TIMEOUT",
        "AKVAN_APPROVAL_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_env_var_wins_over_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\nAKVAN_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("AKVAN_MODEL", "env-model")

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.openrouter_api_key == "env-key"
    assert settings.model == "env-model"


def test_dotenv_loads_when_env_is_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AKVAN_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=dotenv-key\n", encoding="utf-8")

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.openrouter_api_key == "dotenv-key"
    assert settings.model == DEFAULT_MODEL


def test_missing_key_raises_setup_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AKVAN_MODEL", raising=False)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        load_settings(project_root=tmp_path, prompt_for_missing_key=False)


def test_save_settings_writes_provider_key_and_model(tmp_path: Path) -> None:
    env_path = save_settings(
        provider="openrouter",
        model="anthropic/claude-test",
        openrouter_api_key="secret-key",
        project_root=tmp_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AKVAN_PROVIDER=openrouter" in content
    assert "AKVAN_MODEL=anthropic/claude-test" in content
    assert "OPENROUTER_API_KEY=secret-key" in content
    assert "AKVAN_OPENROUTER_API_MODE" not in content


def test_legacy_openrouter_api_mode_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\n"
        "AKVAN_OPENROUTER_API_MODE=responses\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.provider == "openrouter"
    assert not hasattr(settings, "openrouter_api_mode")


def test_openai_codex_api_key_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "AKVAN_PROVIDER=openai-codex\n"
        "AKVAN_CODEX_AUTH_MODE=api-key\n"
        "OPENAI_API_KEY=openai-key\n"
        "AKVAN_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.provider == "openai-codex"
    assert settings.codex_auth_mode == "api-key"
    assert settings.openai_api_key == "openai-key"
    assert settings.model == "gpt-5.5"


def test_openai_codex_cli_settings_do_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    auth_path = tmp_path / "codex-auth.json"
    (tmp_path / ".env").write_text(
        "AKVAN_PROVIDER=openai-codex\n"
        "AKVAN_CODEX_AUTH_MODE=cli\n"
        f"AKVAN_CODEX_AUTH_PATH={auth_path}\n"
        "AKVAN_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.provider == "openai-codex"
    assert settings.codex_auth_mode == "cli"
    assert settings.codex_cli_auth_path == str(auth_path)


def test_openai_codex_cli_is_default_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "AKVAN_PROVIDER=openai-codex\n"
        "AKVAN_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.provider == "openai-codex"
    assert settings.codex_auth_mode == "cli"
    assert settings.codex_cli_auth_path == ""


def test_save_settings_writes_codex_auth_values(tmp_path: Path) -> None:
    env_path = save_settings(
        provider="openai-codex",
        model="gpt-5.5",
        openrouter_api_key="",
        openai_api_key="openai-key",
        codex_auth_mode="api-key",
        project_root=tmp_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AKVAN_PROVIDER=openai-codex" in content
    assert "AKVAN_MODEL=gpt-5.5" in content
    assert "AKVAN_CODEX_AUTH_MODE=api-key" in content
    assert "OPENAI_API_KEY=openai-key" in content


def test_deepseek_settings_require_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "AKVAN_PROVIDER=deepseek\n"
        "AKVAN_MODEL=deepseek-v4-pro\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        load_settings(project_root=tmp_path, prompt_for_missing_key=False)


def test_deepseek_settings_load_thinking_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "AKVAN_PROVIDER=deepseek\n"
        "DEEPSEEK_API_KEY=deepseek-key\n"
        "AKVAN_MODEL=deepseek-v4-pro\n"
        "AKVAN_DEEPSEEK_THINKING=disabled\n"
        "AKVAN_DEEPSEEK_REASONING_EFFORT=max\n"
        "DEEPSEEK_BASE_URL=https://example.test/v1\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == "deepseek-key"
    assert settings.deepseek_thinking == "disabled"
    assert settings.deepseek_reasoning_effort == "max"
    assert settings.deepseek_base_url == "https://example.test/v1"


def test_save_settings_writes_deepseek_key(tmp_path: Path) -> None:
    env_path = save_settings(
        provider="deepseek",
        model="deepseek-chat",
        deepseek_api_key="deepseek-key",
        project_root=tmp_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AKVAN_PROVIDER=deepseek" in content
    assert "AKVAN_MODEL=deepseek-chat" in content
    assert "DEEPSEEK_API_KEY=deepseek-key" in content


def test_agent_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.max_iterations == DEFAULT_MAX_ITERATIONS
    assert settings.yolo is False
    assert settings.approval_mode == "ask"
    assert settings.terminal_timeout == 120


def test_agent_settings_load_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\n"
        "AKVAN_MAX_ITERATIONS=12\n"
        "AKVAN_APPROVAL_MODE=deny\n"
        "AKVAN_TERMINAL_TIMEOUT=45\n"
        "AKVAN_YOLO=true\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, prompt_for_missing_key=False)

    assert settings.max_iterations == 12
    assert settings.approval_mode == "deny"
    assert settings.terminal_timeout == 45
    assert settings.yolo is True


def test_save_agent_settings_writes_runtime_keys(tmp_path: Path) -> None:
    env_path = save_agent_settings(
        max_iterations=25,
        approval_mode="off",
        terminal_timeout=90,
        yolo=True,
        project_root=tmp_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AKVAN_MAX_ITERATIONS=25" in content
    assert "AKVAN_APPROVAL_MODE=off" in content
    assert "AKVAN_TERMINAL_TIMEOUT=90" in content
    assert "AKVAN_YOLO=true" in content
    assert "AKVAN_PROVIDER=" not in content


def test_invalid_max_iterations_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\n"
        "AKVAN_MAX_ITERATIONS=0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="AKVAN_MAX_ITERATIONS"):
        load_settings(project_root=tmp_path, prompt_for_missing_key=False)


def test_invalid_yolo_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-key\n"
        "AKVAN_YOLO=maybe\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="AKVAN_YOLO"):
        load_settings(project_root=tmp_path, prompt_for_missing_key=False)

