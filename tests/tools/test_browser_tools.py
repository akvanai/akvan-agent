from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config import resolve_enabled_toolsets
from agent.skills import SkillRegistry
from agent.tools.banner_generation import list_templates, normalize_banner_size
from agent.tools.browser import build_browser_tools
from agent.tools.registry import build_registry
from agent.tools.browser_runtime.config import (
    CONTAINER_PROFILES_DIR,
    DEFAULT_RUNTIME_PORT,
    save_browser_tools_yaml,
)
from agent.tools.browser_runtime import client as browser_client
from agent.tools.browser_runtime import docker as browser_docker
from agent.tools.browser_runtime.profiles import (
    ProfileError,
    import_storage_state,
    list_profiles,
    migrate_legacy_x_auth,
    profile_status,
    validate_profile_name,
)
from agent.tools.browser_runtime.session_ops import BrowserSession
from agent.tools.process_manager import ProcessManager


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


def _write_storage_state(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cookies": [{"name": "sid", "value": "1", "domain": ".example.com", "path": "/"}]}),
        encoding="utf-8",
    )
    return path


def test_browser_toolsets_disabled_without_config(tmp_path: Path) -> None:
    names = _tool_names(tmp_path)
    assert "banner_render" not in names
    assert "browser_start" not in names


def test_banner_toolset_enabled_without_browser_tools(tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "local", "host": "127.0.0.1", "port": 49732},
        banner_generation={"enabled": True},
        project_root=tmp_path,
    )

    names = _tool_names(tmp_path)

    assert "banner_list_templates" in names
    assert "banner_render" in names
    assert "browser_start" not in names


def test_browser_toolset_loads_when_enabled(tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        browser={"enabled": True},
        project_root=tmp_path,
    )

    names = _tool_names(tmp_path)

    assert "browser_list_profiles" in names
    assert "browser_start" in names
    assert "browser_snapshot" in names
    assert "browser_click" in names
    assert "browser_upload" in names


def test_browser_tools_use_global_config_from_project(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49732},
        browser={"enabled": True},
    )

    names = _tool_names(project)

    assert "browser_start" in names
    assert "browser_auth_status" in names


def test_browser_project_config_overrides_global(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        browser={"enabled": True},
    )
    save_browser_tools_yaml(
        browser={"enabled": False},
        project_root=project,
    )

    assert "browser_start" not in _tool_names(project)


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


def test_profile_import_and_status(tmp_path: Path) -> None:
    source = _write_storage_state(tmp_path / "incoming.json")
    result = import_storage_state("x", source, project_root=tmp_path)
    assert result["ok"] is True
    status = profile_status("x", project_root=tmp_path)
    assert status["ready"] is True
    assert status["auth_file_exists"] is True
    listed = list_profiles(project_root=tmp_path)
    assert [item["name"] for item in listed] == ["x"]


def test_profile_rejects_invalid_storage_state(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"origins": []}', encoding="utf-8")
    with pytest.raises(ProfileError, match="cookies"):
        import_storage_state("x", bad, project_root=tmp_path)


def test_migrate_legacy_x_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "akvan-home"))
    home = Path(tmp_path / "akvan-home")
    legacy = home / "x" / "auth.json"
    _write_storage_state(legacy)
    migrated = migrate_legacy_x_auth()
    assert migrated is not None
    assert migrated["name"] == "x"
    assert profile_status("x")["ready"] is True
    assert migrate_legacy_x_auth() is None


def test_migrate_legacy_x_account_config_enables_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.tools.browser_runtime.config import (
        is_browser_configured,
        migrate_legacy_x_account_config,
    )

    home = tmp_path / "akvan-home"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    _write_storage_state(home / "x" / "auth.json")
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "local", "host": "127.0.0.1", "port": 49733},
        x_account={"enabled": True, "auth_state_path": str(home / "x" / "auth.json")},
    )

    result = migrate_legacy_x_account_config()
    assert result["changed"] is True
    assert result["browser_enabled"] is True
    assert is_browser_configured() is True
    assert "browser_start" in _tool_names(tmp_path / "project")
    # Second call is idempotent.
    assert migrate_legacy_x_account_config()["changed"] is False


def test_validate_profile_name() -> None:
    assert validate_profile_name("github") == "github"
    with pytest.raises(ProfileError):
        validate_profile_name("../etc")


def test_browser_auth_status_missing_profile_is_safe(tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        browser={"enabled": True},
        project_root=tmp_path,
    )
    tools = {tool.name: tool for tool in build_browser_tools(project_root=tmp_path)}
    result = json.loads(tools["browser_auth_status"].invoke({"profile": "x"}).content)
    assert result["ok"] is False
    assert result["ready"] is False


def test_session_ref_resolve_requires_snapshot() -> None:
    session = BrowserSession(inactivity_timeout_seconds=60)
    with pytest.raises(Exception, match="No browser session"):
        session.click("e1")


def test_session_upload_requires_open_session() -> None:
    session = BrowserSession(inactivity_timeout_seconds=60)
    with pytest.raises(Exception, match="No browser session"):
        session.upload(["/tmp/missing.png"])


def test_encode_and_materialize_upload_files(tmp_path: Path) -> None:
    import base64

    from agent.tools.browser_runtime import upload_paths as upload_mod

    src = tmp_path / "shot.png"
    src.write_bytes(b"png-bytes")
    encoded = upload_mod.encode_upload_files([str(src)])
    assert len(encoded) == 1
    assert encoded[0]["name"] == "shot.png"
    assert base64.b64decode(encoded[0]["content_base64"]) == b"png-bytes"

    dest = tmp_path / "out"
    paths = upload_mod.materialize_upload_files(encoded, dest_dir=dest)
    assert len(paths) == 1
    assert Path(paths[0]).read_bytes() == b"png-bytes"

    with pytest.raises(upload_mod.UploadPathError, match="not found"):
        upload_mod.encode_upload_files([str(tmp_path / "missing.png")])


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
    assert "--profiles-dir" in popen_calls[0]


def test_docker_runtime_lifecycle_uses_profiles_mount(monkeypatch, tmp_path: Path) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49992},
        browser={"enabled": True},
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
    assert f"AKVAN_BROWSER_PROFILES_DIR={CONTAINER_PROFILES_DIR}" in command
    assert any(CONTAINER_PROFILES_DIR in part for part in command)
    assert "akvan.runtime.api=4" in command
    assert not any("/akvan-vault" in part for part in command)
    assert not any("/akvan-banners" in part for part in command)


def test_ensure_docker_runtime_recreates_when_app_mount_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49994},
        project_root=tmp_path,
    )
    commands: list[list[str]] = []
    existing = {
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "app": "akvan-agent-browser-runtime",
                "akvan.runtime.port": "49994",
                "akvan.runtime.image": browser_docker.DEFAULT_DOCKER_IMAGE,
                "akvan.runtime.api": browser_docker.RUNTIME_API_VERSION,
            }
        },
        "NetworkSettings": {"Ports": {"49994/tcp": [{"HostPort": "49994"}]}},
    }

    monkeypatch.setattr(browser_docker, "_inspect", lambda name: existing)
    monkeypatch.setattr(browser_docker, "_require_docker", lambda: None)
    monkeypatch.setattr(browser_docker, "_ensure_runtime_image", lambda **kwargs: None)
    monkeypatch.setattr(browser_docker, "_app_mount_healthy", lambda name, container: False)
    monkeypatch.setattr(browser_docker, "_run", lambda cmd: commands.append(list(cmd)) or "ok")

    browser_docker.ensure_docker_runtime(
        config={
            "mode": "docker",
            "host": "127.0.0.1",
            "port": 49994,
            "image": browser_docker.DEFAULT_DOCKER_IMAGE,
        },
        project_root=tmp_path,
    )

    assert ["docker", "rm", "-f", "akvan-agent-browser-runtime"] in commands
    assert any(cmd[:3] == ["docker", "run", "-d"] for cmd in commands)


def test_client_recreates_docker_when_app_mount_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_browser_tools_yaml(
        browser_runtime={"enabled": True, "mode": "docker", "host": "127.0.0.1", "port": 49995},
        project_root=tmp_path,
    )
    calls: list[str] = []
    recreated: list[bool] = []

    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = browser_client.httpx.Request("POST", "http://example/banner/render")
                response = browser_client.httpx.Response(
                    self.status_code, request=request, text=self.text
                )
                raise browser_client.httpx.HTTPStatusError(
                    "error", request=request, response=response
                )

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        calls.append(url)
        if len(calls) == 1:
            return Response(
                500,
                {
                    "ok": False,
                    "error": "banner_render_failed",
                    "message": (
                        "[Errno 2] No such file or directory: "
                        "'/app/agent/tools/browser_runtime/banner_renderer.py'"
                    ),
                },
            )
        return Response(200, {"ok": True, "png_base64": "QQ=="})

    monkeypatch.setattr(browser_client.httpx, "request", fake_request)
    monkeypatch.setattr(
        browser_client.BrowserRuntimeClient,
        "_recreate_docker_runtime",
        lambda self: recreated.append(True),
    )
    monkeypatch.setattr(browser_client.BrowserRuntimeClient, "_wait_until_ready", lambda self: None)

    result = browser_client.BrowserRuntimeClient(project_root=tmp_path).post(
        "/banner/render", {"html": "<div/>", "css": "div{}", "width": 100, "height": 100}
    )

    assert recreated == [True]
    assert len(calls) == 2
    assert result["ok"] is True


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
