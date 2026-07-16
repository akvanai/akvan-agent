"""Configuration loading and persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import DEFAULT_MODEL, load_settings, save_settings


@pytest.fixture(autouse=True)
def isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home" / ".akvan"))


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

