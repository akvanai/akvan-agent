"""Tools setup wizard tests."""

from __future__ import annotations

from agent.tools.browser_runtime.config import (
    browser_runtime_config,
    is_banner_generation_configured,
    save_browser_tools_yaml,
)
from agent.tools.web.config import (
    is_extract_configured,
    is_search_configured,
    load_web_yaml,
    save_web_env,
    save_web_yaml,
)
from agent.ui.app import build_parser
from agent.ui.setup import SELECTOR_SEPARATOR
from agent.ui.tools_setup import (
    _art_menu_items,
    _browser_menu_items,
    _ensure_docker_available_with_prompt,
    _browser_runtime_mode_items,
    _extract_status,
    _main_menu_items,
    _search_provider_items,
    _social_menu_items,
    _tools_category_items,
)


def test_tools_command_is_available() -> None:
    args = build_parser().parse_args(["tools"])
    assert args.command == "tools"


def test_save_web_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    env_path = save_web_env(
        {
            "AKVAN_WEB_SEARCH_BACKEND": "searxng",
            "SEARXNG_URL": "http://localhost:8080",
        },
        project_root=tmp_path,
    )
    yaml_path = save_web_yaml(search_backend="searxng", project_root=tmp_path)
    assert env_path == tmp_path / ".env"
    assert yaml_path == tmp_path / "config.yaml"
    assert load_web_yaml(project_root=tmp_path).get("search_backend") == "searxng"
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
    assert is_search_configured(project_root=tmp_path)


def test_save_browser_tools_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))
    path = save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49999},
        banner_generation={"enabled": True, "root_dir": str(tmp_path / "banners")},
        project_root=tmp_path,
    )

    assert path == tmp_path / "config.yaml"
    assert browser_runtime_config(project_root=tmp_path)["mode"] == "docker"
    assert is_banner_generation_configured(project_root=tmp_path)


def test_tools_category_menu_is_one_line_and_emoji_led(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))

    items = _tools_category_items()
    labels = [label for _, label in items]

    assert labels[0].startswith("🔎 Search Web  ")
    assert labels[1].startswith("🌐 Browser Tool  runtime=")
    assert labels[2].startswith("🐦 Social Media  X=")
    assert "telegram=" in labels[2]
    assert labels[3].startswith("🎨 Art and Content Creation  banner=")
    assert all(
        "\n" not in label for value, label in items if value != SELECTOR_SEPARATOR
    )


def test_tools_submenus_are_one_line(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))

    menu_labels = [
        label
        for menus in (
            _main_menu_items(),
            _browser_menu_items(),
            _social_menu_items(),
            _art_menu_items(),
            _search_provider_items(),
        )
        for value, label in menus
        if value != SELECTOR_SEPARATOR
    ]

    assert menu_labels
    assert all("\n" not in label for label in menu_labels)


def test_web_menu_has_search_extract_and_back_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))

    keys = [key for key, _ in _main_menu_items()]
    assert keys == ["search", "extract", SELECTOR_SEPARATOR, "back"]


def test_extract_status_is_active_by_default() -> None:
    assert is_extract_configured()
    assert _extract_status() == "active"


def test_tools_menus_separate_footer_items(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))

    for items, footer_keys in (
        (_main_menu_items(), ("back",)),
        (_browser_menu_items(), ("back",)),
        (_social_menu_items(), ("back",)),
        (_art_menu_items(), ("back",)),
        (_search_provider_items(), ("back",)),
        (_tools_category_items(), ("back",)),
    ):
        separator_indexes = [
            index for index, (key, _) in enumerate(items) if key == SELECTOR_SEPARATOR
        ]
        assert len(separator_indexes) == 1
        separator_index = separator_indexes[0]
        assert items[separator_index - 1][0] not in footer_keys
        assert [key for key, _ in items[separator_index + 1 :]] == list(footer_keys)


def test_single_item_category_menus_have_back() -> None:
    for items, item_key in (
        (_browser_menu_items(), "runtime"),
        (_art_menu_items(), "banner"),
    ):
        keys = [key for key, _ in items]
        assert keys == [item_key, SELECTOR_SEPARATOR, "back"]


def test_social_menu_includes_x_and_telegram_delivery() -> None:
    keys = [key for key, _ in _social_menu_items()]
    assert keys == ["x", "telegram_delivery", SELECTOR_SEPARATOR, "back"]


def test_docker_install_prompt_runs_only_after_confirmation(monkeypatch) -> None:
    commands = [["apt-get", "install", "-y", "docker.io"]]
    installed = []

    monkeypatch.setattr("agent.ui.tools_setup._docker_is_available", lambda: False)
    monkeypatch.setattr("agent.ui.tools_setup._docker_install_commands", lambda: commands)
    monkeypatch.setattr("agent.ui.tools_setup.run_full_screen_selector", lambda **kwargs: "install")
    monkeypatch.setattr(
        "agent.ui.tools_setup.run_full_screen_task",
        lambda **kwargs: kwargs["callback"](),
    )
    monkeypatch.setattr(
        "agent.ui.tools_setup._install_docker",
        lambda install_commands: installed.append(install_commands) or "ok",
    )

    assert _ensure_docker_available_with_prompt(title="Docker") is True
    assert installed == [commands]


def test_docker_install_prompt_respects_cancel(monkeypatch) -> None:
    installed = []

    monkeypatch.setattr("agent.ui.tools_setup._docker_is_available", lambda: False)
    monkeypatch.setattr(
        "agent.ui.tools_setup._docker_install_commands",
        lambda: [["apt-get", "install", "-y", "docker.io"]],
    )
    monkeypatch.setattr("agent.ui.tools_setup.run_full_screen_selector", lambda **kwargs: "cancel")
    monkeypatch.setattr(
        "agent.ui.tools_setup._install_docker",
        lambda install_commands: installed.append(install_commands) or "ok",
    )

    assert _ensure_docker_available_with_prompt(title="Docker") is False
    assert installed == []


def test_docker_install_prompt_handles_unsupported_system(monkeypatch) -> None:
    messages = []

    monkeypatch.setattr("agent.ui.tools_setup._docker_is_available", lambda: False)
    monkeypatch.setattr("agent.ui.tools_setup._docker_install_commands", lambda: [])
    monkeypatch.setattr(
        "agent.ui.tools_setup.run_full_screen_message",
        lambda **kwargs: messages.append(kwargs),
    )

    assert _ensure_docker_available_with_prompt(title="Docker") is False
    assert messages[0]["title"] == "Docker not available"


def test_browser_runtime_mode_menu_recommends_docker_and_has_back() -> None:
    items = _browser_runtime_mode_items()
    labels = {key: label for key, label in items if key != SELECTOR_SEPARATOR}

    assert "recommended" not in labels["local"].lower()
    assert "recommended" in labels["docker"].lower()
    assert items[-1][0] == "back"
    assert items[-2][0] == SELECTOR_SEPARATOR
