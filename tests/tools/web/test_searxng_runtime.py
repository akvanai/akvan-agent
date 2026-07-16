"""Tests for managed local SearXNG runtime."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.tools.web.config import load_web_yaml, save_web_env, save_web_yaml, searxng_runtime_config
from agent.tools.web.searxng_runtime.config import BUNDLE_DIR, runtime_config_dir
from agent.tools.web.searxng_runtime.docker import (
    ensure_searxng_runtime,
    has_matching_searxng_runtime,
    remove_searxng_runtime,
)
from agent.tools.web.searxng_runtime.ports import is_port_free, suggest_next_port


@pytest.fixture(autouse=True)
def _isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path))


def test_bundled_settings_disable_valkey_and_limiter() -> None:
    settings = (BUNDLE_DIR / "searxng_settings.yml").read_text(encoding="utf-8")
    assert "valkey:" in settings
    assert "url: false" in settings
    assert "limiter: false" in settings
    assert "akvan_redis" not in settings
    assert "Akvan Search" not in settings


def test_materialize_runtime_config_copies_bundle_files(tmp_path: Path) -> None:
    from agent.tools.web.searxng_runtime.docker import _materialize_runtime_config

    config_dir = _materialize_runtime_config(project_root=tmp_path)
    assert config_dir == runtime_config_dir(project_root=tmp_path)
    assert (config_dir / "searxng_settings.yml").is_file()
    assert (config_dir / "searxng_limiter.toml").is_file()
    assert (config_dir / "data").is_dir()


def test_suggest_next_port_skips_busy_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    host = "127.0.0.1"
    busy = suggest_next_port(host, 49152)

    def fake_is_port_free(check_host: str, port: int) -> bool:
        return port != busy

    monkeypatch.setattr(
        "agent.tools.web.searxng_runtime.ports.is_port_free",
        fake_is_port_free,
    )
    assert suggest_next_port(host, busy) == busy + 1


def test_is_port_free_detects_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert not is_port_free("127.0.0.1", port)


def test_ensure_searxng_runtime_starts_container_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return ""
        if cmd[:2] == ["docker", "version"]:
            return "27.0.0"
        return "container-id"

    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._run", fake_run)
    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: None)
    monkeypatch.setattr(
        "agent.tools.web.searxng_runtime.docker.get_env_value",
        lambda key, **kwargs: "test-secret" if key == "SEARXNG_SECRET" else "",
    )

    url = ensure_searxng_runtime(port=8090, host="127.0.0.1", project_root=tmp_path)

    assert url == "http://127.0.0.1:8090"
    run_cmd = next(cmd for cmd in commands if cmd[:2] == ["docker", "run"])
    assert "akvan-agent-searxng" in run_cmd
    assert "127.0.0.1:8090:8080" in run_cmd
    assert "SEARXNG_SECRET=test-secret" in run_cmd
    assert "SEARXNG_INSTANCE_NAME=Local Search" in run_cmd


def test_ensure_searxng_runtime_reuses_matching_container(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    container = {
        "State": {"Running": False},
        "Config": {
            "Labels": {
                "app": "akvan-agent-searxng",
                "akvan.searxng.port": "8090",
                "akvan.searxng.host": "127.0.0.1",
                "akvan.searxng.image": "searxng/searxng:latest",
            }
        },
        "NetworkSettings": {
            "Ports": {
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8090"}],
            }
        },
    }

    def fake_run(cmd: list[str], **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["docker", "version"]:
            return "27.0.0"
        return ""

    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._run", fake_run)
    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: container)

    url = ensure_searxng_runtime(port=8090, host="127.0.0.1")

    assert url == "http://127.0.0.1:8090"
    assert ["docker", "start", "akvan-agent-searxng"] in commands
    assert not any(cmd[:2] == ["docker", "run"] for cmd in commands)


def test_has_matching_searxng_runtime_detects_owned_port(monkeypatch: pytest.MonkeyPatch) -> None:
    container = {
        "Config": {
            "Labels": {
                "app": "akvan-agent-searxng",
                "akvan.searxng.port": "8090",
                "akvan.searxng.host": "127.0.0.1",
                "akvan.searxng.image": "searxng/searxng:latest",
            }
        },
        "NetworkSettings": {
            "Ports": {
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8090"}],
            }
        },
    }

    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: container)

    assert has_matching_searxng_runtime(port=8090, host="127.0.0.1") is True
    assert has_matching_searxng_runtime(port=8091, host="127.0.0.1") is False


def test_remove_searxng_runtime_removes_labeled_container(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    container = {
        "Config": {"Labels": {"app": "akvan-agent-searxng"}},
    }

    def fake_run(cmd: list[str], **kwargs):
        commands.append(cmd)
        return ""

    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._run", fake_run)
    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: container)

    removed = remove_searxng_runtime()

    assert removed is True
    assert commands == [["docker", "rm", "-f", "akvan-agent-searxng"]]


def test_remove_searxng_runtime_skips_unrelated_container(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    container = {
        "Config": {"Labels": {"app": "something-else"}},
    }

    monkeypatch.setattr(
        "agent.tools.web.searxng_runtime.docker._run",
        lambda cmd, **kwargs: commands.append(cmd),
    )
    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: container)

    removed = remove_searxng_runtime()

    assert removed is False
    assert commands == []


def test_remove_searxng_runtime_noop_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "agent.tools.web.searxng_runtime.docker._run",
        lambda cmd, **kwargs: commands.append(cmd),
    )
    monkeypatch.setattr("agent.tools.web.searxng_runtime.docker._inspect", lambda _name: None)

    assert remove_searxng_runtime() is False
    assert commands == []


def test_save_web_yaml_can_clear_searxng_section(tmp_path: Path) -> None:
    save_web_yaml(
        search_backend="searxng",
        searxng={"mode": "managed", "port": 8090, "host": "127.0.0.1"},
        project_root=tmp_path,
    )
    save_web_yaml(
        search_backend="ddgs",
        clear_searxng=True,
        project_root=tmp_path,
    )

    web_cfg = load_web_yaml(project_root=tmp_path)
    assert web_cfg["search_backend"] == "ddgs"
    assert "searxng" not in web_cfg


def test_save_web_yaml_persists_managed_searxng_section(tmp_path: Path) -> None:
    save_web_yaml(
        search_backend="searxng",
        searxng={"mode": "managed", "port": 8090, "host": "127.0.0.1"},
        project_root=tmp_path,
    )
    save_web_env(
        {"SEARXNG_URL": "http://127.0.0.1:8090", "AKVAN_WEB_SEARCH_BACKEND": "searxng"},
        project_root=tmp_path,
    )

    web_cfg = load_web_yaml(project_root=tmp_path)
    assert web_cfg["search_backend"] == "searxng"
    assert web_cfg["searxng"]["mode"] == "managed"
    assert web_cfg["searxng"]["port"] == 8090
    runtime_cfg = searxng_runtime_config(project_root=tmp_path)
    assert runtime_cfg["mode"] == "managed"
    assert runtime_cfg["port"] == 8090
