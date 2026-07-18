from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config import resolve_enabled_toolsets
from agent.skills import SkillRegistry
from agent.tools.banner_generation import list_templates, normalize_banner_size
from agent.tools.registry import build_registry
from agent.tools.browser_runtime.config import DEFAULT_RUNTIME_PORT, save_browser_tools_yaml
from agent.tools.browser_runtime import client as browser_client
from agent.tools.browser_runtime import docker as browser_docker
from agent.tools.process_manager import ProcessManager
from agent.tools.x_account import build_x_account_tools


@pytest.fixture(autouse=True)
def _isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "akvan-home"))


def _tool_names(tmp_path: Path) -> set[str]:
    skills = SkillRegistry.discover(user_root=tmp_path / "home", project_root=tmp_path)
    manager = ProcessManager()
    try:
        registry = build_registry(skills, project_root=tmp_path, process_manager=manager)
        tools = registry.resolve(resolve_enabled_toolsets(project_root=tmp_path))
        return {tool.name for tool in tools}
    finally:
        manager.cleanup()


def test_browser_toolsets_disabled_without_config(tmp_path: Path) -> None:
    names = _tool_names(tmp_path)
    assert "banner_render" not in names
    assert "x_post" not in names


def test_banner_toolset_enabled_without_x(tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "local", "host": "127.0.0.1", "port": 49732},
        banner_generation={"enabled": True},
        project_root=tmp_path,
    )

    names = _tool_names(tmp_path)

    assert "banner_list_templates" in names
    assert "banner_render" in names
    assert "x_post" not in names


def test_x_toolset_loads_status_before_auth_exists(tmp_path: Path) -> None:
    auth = tmp_path / "x" / "auth.json"
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        x_account={"enabled": True, "auth_state_path": str(auth)},
        project_root=tmp_path,
    )

    names = _tool_names(tmp_path)

    assert "x_auth_status" in names
    assert "x_post" in names


def test_browser_tools_use_global_config_from_project(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49732},
        x_account={"enabled": True, "auth_state_path": str(home / "x" / "auth.json")},
    )

    names = _tool_names(project)

    assert "x_auth_status" in names
    assert "x_post" in names


def test_browser_project_config_overrides_global(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        x_account={"enabled": True, "auth_state_path": str(home / "x" / "auth.json")},
    )
    save_browser_tools_yaml(
        x_account={"enabled": False},
        project_root=project,
    )

    assert "x_post" not in _tool_names(project)


def test_banner_size_presets_and_custom_dimensions() -> None:
    assert normalize_banner_size({"preset": "x_landscape"}) == {
        "preset": "x_landscape",
        "width": 1200,
        "height": 675,
    }
    assert normalize_banner_size({"preset": "custom", "width": 800, "height": 600}) == {
        "preset": "custom",
        "width": 800,
        "height": 600,
    }
    with pytest.raises(ValueError):
        normalize_banner_size({"preset": "custom", "width": 20, "height": 600})


def test_template_lookup_prefers_managed_templates(tmp_path: Path) -> None:
    banner_root = tmp_path / "managed-banners"
    user_template = banner_root / "templates" / "announcement-basic"
    user_template.mkdir(parents=True)
    (user_template / "meta.json").write_text(
        json.dumps({
            "id": "announcement-basic",
            "description": "managed",
            "width": 1200,
            "height": 675,
            "fields": [{"name": "title", "required": True}],
            "sample_data": {"title": "Preview"},
        }),
        encoding="utf-8",
    )
    (user_template / "index.html").write_text("<h1>{{title}}</h1>", encoding="utf-8")
    (user_template / "style.css").write_text("h1 { color: red; }", encoding="utf-8")
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        banner_generation={"enabled": True, "root_dir": str(banner_root)},
        project_root=tmp_path,
    )

    templates = {item["id"]: item for item in list_templates(project_root=tmp_path)}

    assert templates["announcement-basic"]["description"] == "managed"
    assert templates["announcement-basic"]["source"] == "managed"
    assert "model-release-basic" in templates


def test_x_auth_status_missing_auth_is_safe(tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        x_account={"enabled": True, "auth_state_path": str(tmp_path / "missing.json")},
        project_root=tmp_path,
    )
    tools = {tool.name: tool for tool in build_x_account_tools(project_root=tmp_path)}

    result = json.loads(tools["x_auth_status"].invoke({}).content)

    assert result["ok"] is False
    assert result["auth_file_exists"] is False
    assert "auth.json" in result["message"]


def test_x_post_requires_confirmation(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in build_x_account_tools(project_root=tmp_path)}
    with pytest.raises(ValueError, match="explicit user confirmation"):
        tools["x_post"].invoke({"text": "hello", "confirmed": False})


def test_browser_runtime_default_port_is_product_port() -> None:
    assert DEFAULT_RUNTIME_PORT == 49733


def test_local_browser_runtime_autostarts_when_unreachable(monkeypatch, tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "local", "host": "127.0.0.1", "port": 49991},
        project_root=tmp_path,
    )
    calls: list[str] = []
    popen_calls: list[list[str]] = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"ok": True}

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        calls.append(url)
        if len(calls) == 1:
            request = browser_client.httpx.Request(method, url)
            raise browser_client.httpx.ConnectError("connection refused", request=request)
        return Response()

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        popen_calls.append(cmd)
        return object()

    monkeypatch.setattr(browser_client.httpx, "request", fake_request)
    monkeypatch.setattr(browser_client.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(browser_client.BrowserRuntimeClient, "_wait_until_ready", lambda self: None)

    result = browser_client.BrowserRuntimeClient(project_root=tmp_path).get("/health")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert popen_calls
    assert "agent.tools.browser_runtime.server" in popen_calls[0]


def test_docker_runtime_lifecycle_uses_configured_port(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "x" / "auth.json"
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49992},
        x_account={"enabled": True, "auth_state_path": str(auth)},
        project_root=tmp_path,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(browser_docker, "_inspect", lambda name: None)
    monkeypatch.setattr(browser_docker, "_require_docker", lambda: None)
    monkeypatch.setattr(browser_docker, "_ensure_runtime_image", lambda **kwargs: None)
    monkeypatch.setattr(browser_docker, "_run", lambda cmd: commands.append(cmd) or "ok")

    browser_docker.ensure_docker_runtime(
        config={"mode": "docker", "host": "127.0.0.1", "port": 49992},
        project_root=tmp_path,
    )

    command = commands[0]
    assert command[:3] == ["docker", "run", "-d"]
    assert "127.0.0.1:49992:49992" in command
    assert "PYTHONPATH=/app" in command
    assert "/app/agent/tools/browser_runtime/server.py" in command
    assert f"AKVAN_X_AUTH_STATE_PATH=/akvan-auth/{auth.name}" in command


def test_docker_browser_runtime_autostarts_when_unreachable(monkeypatch, tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49993},
        project_root=tmp_path,
    )
    calls: list[str] = []
    started: list[bool] = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"ok": True}

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        calls.append(url)
        if len(calls) == 1:
            request = browser_client.httpx.Request(method, url)
            raise browser_client.httpx.ConnectError("connection refused", request=request)
        return Response()

    monkeypatch.setattr(browser_client.httpx, "request", fake_request)
    monkeypatch.setattr(browser_client, "ensure_docker_runtime", lambda **kwargs: started.append(True))
    monkeypatch.setattr(browser_client.BrowserRuntimeClient, "_wait_until_ready", lambda self: None)

    result = browser_client.BrowserRuntimeClient(project_root=tmp_path).get("/health")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert started == [True]


def test_remove_docker_runtime_removes_labeled_container(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    container = {
        "Config": {"Labels": {"app": "akvan-agent-browser-runtime"}},
    }

    monkeypatch.setattr(browser_docker, "_run", lambda cmd, **kwargs: commands.append(cmd) or "")
    monkeypatch.setattr(browser_docker, "_inspect", lambda _name: container)

    removed = browser_docker.remove_docker_runtime()

    assert removed is True
    assert commands == [["docker", "rm", "-f", "akvan-agent-browser-runtime"]]


def test_remove_docker_runtime_skips_unrelated_container(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    container = {
        "Config": {"Labels": {"app": "something-else"}},
    }

    monkeypatch.setattr(browser_docker, "_run", lambda cmd, **kwargs: commands.append(cmd))
    monkeypatch.setattr(browser_docker, "_inspect", lambda _name: container)

    assert browser_docker.remove_docker_runtime() is False
    assert commands == []


def test_is_docker_browser_runtime(tmp_path: Path) -> None:
    from agent.tools.browser_runtime.config import is_docker_browser_runtime

    assert is_docker_browser_runtime(project_root=tmp_path) is False
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49733},
        project_root=tmp_path,
    )
    assert is_docker_browser_runtime(project_root=tmp_path) is True
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "local", "host": "127.0.0.1", "port": 49733},
        project_root=tmp_path,
    )
    assert is_docker_browser_runtime(project_root=tmp_path) is False
