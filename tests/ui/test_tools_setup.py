"""Tools setup wizard tests."""

from __future__ import annotations

from agent.tools.web.config import (
    is_search_configured,
    load_web_yaml,
    save_web_env,
    save_web_yaml,
)
from agent.ui.app import build_parser


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
