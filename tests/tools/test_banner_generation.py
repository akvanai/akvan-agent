"""Managed banner template and render workflow tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from agent.tools.banner_generation import (
    build_banner_generation_tools,
    ensure_banner_workspace,
    get_template,
    render_template,
    save_template,
    telegram_review_status,
)
from agent.tools.browser_runtime import banner_renderer
from agent.tools.browser_runtime.config import banner_generation_config, save_browser_tools_yaml


@pytest.fixture(autouse=True)
def _isolate_akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "akvan-home"))


def _enable(tmp_path: Path) -> Path:
    root = tmp_path / "managed-banners"
    save_browser_tools_yaml(
        browser_runtime={"enabled": True},
        banner_generation={"enabled": True, "root_dir": str(root)},
        project_root=tmp_path,
    )
    return root


def _meta() -> dict[str, object]:
    return {
        "name": "Launch card",
        "description": "Reusable launch announcement",
        "width": 800,
        "height": 500,
        "fields": [
            {"name": "title", "type": "text", "required": True},
            {"name": "eyebrow", "type": "text", "default": "NEW"},
        ],
        "sample_data": {"title": "A useful preview"},
    }


def test_banner_workspace_keeps_all_artifacts_together(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "akvan-home"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    save_browser_tools_yaml(
        banner_generation={
            "enabled": True,
            "templates_dir": "/opt/unrelated/templates",
            "output_dir": "/opt/unrelated/renders",
        },
        project_root=tmp_path,
    )

    config = banner_generation_config(project_root=tmp_path)
    directories = ensure_banner_workspace(project_root=tmp_path)

    assert config["root_dir"] == home / "banners"
    assert directories["templates"] == home / "banners" / "templates"
    assert directories["renders"] == home / "banners" / "renders"
    assert directories["assets"] == home / "banners" / "assets"
    assert all(path.is_dir() for path in directories.values())


def test_save_template_writes_html_css_and_meta(tmp_path: Path) -> None:
    root = _enable(tmp_path)

    result = save_template(
        "launch-card",
        html="<!doctype html><h1>{{title}}</h1><span>{{eyebrow}}</span>",
        css="h1 { color: white; }",
        meta=_meta(),
        project_root=tmp_path,
    )

    target = root / "templates" / "launch-card"
    assert result["files"] == ["index.html", "style.css", "meta.json"]
    assert {path.name for path in target.iterdir()} == {
        "index.html", "style.css", "meta.json",
    }
    loaded = get_template("launch-card", project_root=tmp_path)
    assert loaded["source"] == "managed"
    assert loaded["meta"]["sample_data"]["title"] == "A useful preview"


def test_save_template_rejects_undeclared_placeholder(tmp_path: Path) -> None:
    _enable(tmp_path)
    with pytest.raises(ValueError, match="missing from meta.fields"):
        save_template(
            "broken-card",
            html="<h1>{{not_declared}}</h1>",
            css="h1 { color: white; }",
            meta=_meta(),
            project_root=tmp_path,
        )


def test_banner_save_tool_requires_confirmation(tmp_path: Path) -> None:
    _enable(tmp_path)
    tools = {
        tool.name: tool for tool in build_banner_generation_tools(project_root=tmp_path)
    }
    with pytest.raises(ValueError, match="user confirmation"):
        tools["banner_save_template"].invoke({
            "template": "launch-card",
            "html": "<h1>{{title}}</h1>",
            "css": "h1 { color: white; }",
            "meta": _meta(),
            "confirmed": False,
        })


def test_render_template_uses_runtime_and_writes_managed_png(
    monkeypatch, tmp_path: Path,
) -> None:
    root = _enable(tmp_path)
    save_template(
        "launch-card",
        html="<!doctype html><h1>{{title}}</h1><span>{{eyebrow}}</span>",
        css="h1 { color: white; }",
        meta=_meta(),
        project_root=tmp_path,
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(self, path: str, payload: dict[str, object]):
        calls.append((path, payload))
        return {"ok": True, "png_base64": base64.b64encode(b"png-bytes").decode()}

    monkeypatch.setattr(
        "agent.tools.banner_generation.BrowserRuntimeClient.post", fake_post,
    )
    result = render_template(
        "launch-card",
        data={"title": "Safe <launch>"},
        project_root=tmp_path,
    )

    assert calls[0][0] == "/banner/render"
    assert "Safe &lt;launch&gt;" in calls[0][1]["html"]
    assert calls[0][1]["width"] == 800
    output = Path(result["output_path"])
    assert output.parent == root / "renders"
    assert output.read_bytes() == b"png-bytes"


def test_workspace_status_explains_telegram_setup(monkeypatch, tmp_path: Path) -> None:
    _enable(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DELIVERY_ALLOWED_USERS", raising=False)

    status = telegram_review_status()

    assert status["configured"] is False
    assert "akvan tools" in status["message"]
    assert "/start" in status["message"]


def test_banner_renderer_validates_before_loading_playwright() -> None:
    with pytest.raises(ValueError, match="HTML is required"):
        banner_renderer.render_banner_payload({"css": "body {}", "width": 800, "height": 500})
    with pytest.raises(ValueError, match="between 100 and 4096"):
        banner_renderer.render_banner_payload({
            "html": "<p>Hi</p>", "css": "body {}", "width": 20, "height": 500,
        })


def test_banner_tools_expose_complete_managed_workflow(tmp_path: Path) -> None:
    _enable(tmp_path)
    names = {
        tool.name for tool in build_banner_generation_tools(project_root=tmp_path)
    }
    assert names == {
        "banner_workspace_status",
        "banner_list_templates",
        "banner_get_template",
        "banner_save_template",
        "banner_render",
    }
