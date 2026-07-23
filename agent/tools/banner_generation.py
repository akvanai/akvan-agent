"""Managed reusable HTML/CSS banner templates and Playwright rendering tools."""

from __future__ import annotations

import base64
import html as html_lib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.storage.permissions import ensure_private_dir, ensure_private_file, write_private_file
from agent.tools.base import Tool, ToolImage, ToolResult
from agent.tools.browser_runtime.client import BrowserRuntimeClient
from agent.tools.browser_runtime.config import BANNER_SIZE_PRESETS, banner_generation_config

STARTER_TEMPLATES_DIR = Path(__file__).resolve().parent / "browser_runtime" / "starter_templates"
_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"{{ *([A-Za-z][A-Za-z0-9_]*) *}}")


def ensure_banner_workspace(*, project_root: Path | None = None) -> dict[str, Path]:
    cfg = banner_generation_config(project_root=project_root)
    directories = {
        "root": Path(cfg["root_dir"]),
        "templates": Path(cfg["templates_dir"]),
        "renders": Path(cfg["output_dir"]),
        "assets": Path(cfg["assets_dir"]),
    }
    for path in directories.values():
        ensure_private_dir(path)
    return directories


def normalize_banner_size(
    size: dict[str, Any] | None, *, default: str = "x_landscape",
) -> dict[str, int | str]:
    raw = size or {"preset": default}
    preset = str(raw.get("preset") or default)
    if preset == "custom":
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)
    else:
        if preset not in BANNER_SIZE_PRESETS:
            raise ValueError(f"Unknown banner size preset {preset!r}.")
        width = BANNER_SIZE_PRESETS[preset]["width"]
        height = BANNER_SIZE_PRESETS[preset]["height"]
    if not 100 <= width <= 4096 or not 100 <= height <= 4096:
        raise ValueError("Banner width and height must be between 100 and 4096 pixels.")
    return {"preset": preset, "width": width, "height": height}


def _validate_template_id(template_id: str) -> str:
    normalized = str(template_id or "").strip().lower()
    if not _TEMPLATE_ID_RE.fullmatch(normalized):
        raise ValueError("Template id must use lowercase letters, numbers, and single hyphens.")
    return normalized


def _normalize_fields(raw_fields: object) -> list[dict[str, Any]]:
    if raw_fields is None:
        return []
    if not isinstance(raw_fields, list):
        raise ValueError("meta.fields must be a list.")
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_fields:
        if isinstance(raw, str):
            field = {"name": raw, "type": "text", "required": True}
        elif isinstance(raw, dict):
            field = dict(raw)
        else:
            raise ValueError("Each meta.fields item must be a field name or object.")
        name = str(field.get("name") or "").strip()
        if not _FIELD_NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid template field name: {name!r}.")
        if name in seen:
            raise ValueError(f"Duplicate template field: {name}.")
        seen.add(name)
        field["name"] = name
        field["type"] = str(field.get("type") or "text")
        field["required"] = bool(field.get("required", False))
        fields.append(field)
    return fields


def _normalize_meta(
    template_id: str, meta: dict[str, Any], *, default_size: str,
) -> dict[str, Any]:
    normalized = dict(meta)
    normalized["id"] = template_id
    normalized["name"] = str(normalized.get("name") or template_id.replace("-", " ").title())
    normalized["description"] = str(normalized.get("description") or "")
    normalized["fields"] = _normalize_fields(normalized.get("fields"))
    sample_data = normalized.get("sample_data") or {}
    if not isinstance(sample_data, dict):
        raise ValueError("meta.sample_data must be an object.")
    normalized["sample_data"] = sample_data
    if normalized.get("width") or normalized.get("height"):
        dimensions = normalize_banner_size({
            "preset": "custom",
            "width": normalized.get("width"),
            "height": normalized.get("height"),
        })
    else:
        dimensions = normalize_banner_size(None, default=default_size)
    normalized["width"] = dimensions["width"]
    normalized["height"] = dimensions["height"]
    return normalized


def _template_roots(*, project_root: Path | None = None) -> tuple[tuple[Path, str], ...]:
    cfg = banner_generation_config(project_root=project_root)
    return ((Path(cfg["templates_dir"]), "managed"), (STARTER_TEMPLATES_DIR, "starter"))


def _read_template(path: Path, *, source: str) -> dict[str, Any] | None:
    meta_path = path / "meta.json"
    html_path = path / "index.html"
    css_path = path / "style.css"
    if not (meta_path.is_file() and html_path.is_file() and css_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        html = html_path.read_text(encoding="utf-8")
        css = css_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    template_id = str(meta.get("id") or path.name)
    return {
        "id": template_id,
        "name": str(meta.get("name") or template_id),
        "description": str(meta.get("description") or ""),
        "fields": meta.get("fields") if isinstance(meta.get("fields"), list) else [],
        "sample_data": meta.get("sample_data") if isinstance(meta.get("sample_data"), dict) else {},
        "width": int(meta.get("width") or 1200),
        "height": int(meta.get("height") or 675),
        "source": source,
        "meta": meta,
        "html": html,
        "css": css,
    }


def list_templates(*, project_root: Path | None = None) -> list[dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    for root, source in _template_roots(project_root=project_root):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            template = _read_template(child, source=source)
            if template is None:
                continue
            summary = {
                key: value
                for key, value in template.items()
                if key not in {"meta", "html", "css"}
            }
            templates.setdefault(template["id"], summary)
    return list(templates.values())


def get_template(template_id: str, *, project_root: Path | None = None) -> dict[str, Any]:
    selected = _validate_template_id(template_id)
    for root, source in _template_roots(project_root=project_root):
        template = _read_template(root / selected, source=source)
        if template is not None:
            return template
    raise ValueError(f"Unknown banner template {selected!r}.")


def save_template(
    template_id: str,
    *,
    html: str,
    css: str,
    meta: dict[str, Any],
    overwrite: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    selected = _validate_template_id(template_id)
    if not str(html or "").strip():
        raise ValueError("Template index.html content is required.")
    if not str(css or "").strip():
        raise ValueError("Template style.css content is required.")
    cfg = banner_generation_config(project_root=project_root)
    normalized_meta = _normalize_meta(selected, meta, default_size=str(cfg["default_size"]))
    declared_fields = {field["name"] for field in normalized_meta["fields"]}
    placeholders = set(_PLACEHOLDER_RE.findall(html)) | set(_PLACEHOLDER_RE.findall(css))
    undeclared = sorted(placeholders - declared_fields)
    if undeclared:
        raise ValueError(
            f"Template placeholders are missing from meta.fields: {', '.join(undeclared)}."
        )

    directories = ensure_banner_workspace(project_root=project_root)
    target = directories["templates"] / selected
    if target.exists() and not overwrite:
        raise ValueError(f"Template {selected!r} already exists; set overwrite after user confirmation.")
    ensure_private_dir(target)
    write_private_file(target / "index.html", html.strip() + "\n")
    write_private_file(target / "style.css", css.strip() + "\n")
    write_private_file(
        target / "meta.json",
        json.dumps(normalized_meta, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "ok": True,
        "saved": True,
        "template": selected,
        "directory": str(target),
        "files": ["index.html", "style.css", "meta.json"],
    }


def _render_values(template: dict[str, Any], data: dict[str, Any] | None) -> dict[str, str]:
    meta = template["meta"]
    values: dict[str, Any] = dict(meta.get("sample_data") or {})
    for field in meta.get("fields") or []:
        if isinstance(field, dict) and "default" in field:
            values.setdefault(str(field.get("name")), field["default"])
    values.update(data or {})
    known = {
        str(field.get("name"))
        for field in meta.get("fields") or []
        if isinstance(field, dict)
    }
    unknown = sorted(set(values) - known)
    if unknown:
        raise ValueError(f"Unknown template data fields: {', '.join(unknown)}.")
    missing = [
        str(field.get("name"))
        for field in meta.get("fields") or []
        if isinstance(field, dict)
        and field.get("required")
        and not str(values.get(str(field.get("name")), "")).strip()
    ]
    if missing:
        raise ValueError(f"Missing required template data: {', '.join(missing)}.")
    return {name: html_lib.escape(str(value), quote=True) for name, value in values.items()}


def _substitute(content: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), ""), content)


def telegram_review_status() -> dict[str, Any]:
    from agent.tools.telegram_delivery.config import load_telegram_delivery_settings

    settings = load_telegram_delivery_settings()
    configured = bool(settings.telegram_bot_token and settings.telegram_allowed_users)
    if configured:
        return {
            "configured": True,
            "recipient_count": len(settings.telegram_allowed_users),
            "message": "Use telegram_send_file after the user asks to receive the sample.",
        }
    return {
        "configured": False,
        "message": (
            "Telegram review delivery is not configured. Run `akvan tools`, set up "
            "Telegram delivery under Social Media, then open the bot and send /start."
        ),
    }


def render_template(
    template_id: str,
    *,
    data: dict[str, Any] | None = None,
    size: dict[str, Any] | None = None,
    output_slug: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    template = get_template(template_id, project_root=project_root)
    values = _render_values(template, data)
    rendered_html = _substitute(template["html"], values)
    rendered_css = _substitute(template["css"], values)
    if size is None:
        dimensions = normalize_banner_size({
            "preset": "custom",
            "width": template["width"],
            "height": template["height"],
        })
    else:
        dimensions = normalize_banner_size(size)

    slug = _validate_template_id(output_slug or template["id"])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    directories = ensure_banner_workspace(project_root=project_root)
    output_path = directories["renders"] / f"{slug}-{timestamp}.png"
    payload = BrowserRuntimeClient(project_root=project_root).post(
        "/banner/render",
        {
            "html": rendered_html,
            "css": rendered_css,
            "width": dimensions["width"],
            "height": dimensions["height"],
        },
    )
    encoded = payload.get("png_base64")
    if not payload.get("ok") or not isinstance(encoded, str):
        raise RuntimeError(
            str(payload.get("message") or "Browser runtime did not return a banner image.")
        )
    try:
        png = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("Browser runtime returned an invalid banner image.") from exc
    output_path.write_bytes(png)
    ensure_private_file(output_path)
    return {
        "ok": True,
        "rendered": True,
        "template": template["id"],
        "output_path": str(output_path),
        "width": dimensions["width"],
        "height": dimensions["height"],
        "telegram_review": telegram_review_status(),
    }


def build_banner_generation_tools(*, project_root: Path | None = None) -> tuple[Tool, ...]:
    cfg = banner_generation_config(project_root=project_root)

    def banner_workspace_status() -> str:
        directories = ensure_banner_workspace(project_root=project_root)
        return json.dumps({
            "ok": True,
            "directories": {key: str(path) for key, path in directories.items()},
            "template_format": ["index.html", "style.css", "meta.json"],
            "telegram_review": telegram_review_status(),
        }, ensure_ascii=False, indent=2)

    def banner_list_templates() -> str:
        return json.dumps(
            {"templates": list_templates(project_root=project_root)},
            ensure_ascii=False,
            indent=2,
        )

    def banner_get_template(template: str) -> str:
        return json.dumps(
            get_template(template, project_root=project_root),
            ensure_ascii=False,
            indent=2,
        )

    def banner_save_template(
        template: str,
        html: str,
        css: str,
        meta: dict[str, Any],
        overwrite: bool = False,
        confirmed: bool = False,
    ) -> str:
        if not confirmed:
            raise ValueError(
                "Refusing to save a reusable banner template without user confirmation."
            )
        result = save_template(
            template,
            html=html,
            css=css,
            meta=meta,
            overwrite=overwrite,
            project_root=project_root,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def banner_render(
        template: str | None = None,
        data: dict[str, Any] | None = None,
        size: dict[str, Any] | None = None,
        output_slug: str | None = None,
    ) -> ToolResult:
        result = render_template(
            template or str(cfg["default_template"]),
            data=data,
            size=size,
            output_slug=output_slug,
            project_root=project_root,
        )
        images: tuple[ToolImage, ...] = ()
        output_path = result.get("output_path")
        if isinstance(output_path, str) and output_path.strip():
            images = (
                ToolImage(
                    path=output_path,
                    mime="image/png",
                    question="Visually QA this rendered banner.",
                ),
            )
        return ToolResult(
            json.dumps(result, ensure_ascii=False, indent=2),
            images=images,
        )

    return (
        Tool(
            name="banner_workspace_status",
            description=(
                "Show the Akvan-managed banner directories, required template files, "
                "and Telegram review readiness."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=banner_workspace_status,
        ),
        Tool(
            name="banner_list_templates",
            description="List only Akvan-managed and bundled starter banner templates.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=banner_list_templates,
        ),
        Tool(
            name="banner_get_template",
            description=(
                "Read a reusable banner template including index.html, style.css, and meta.json."
            ),
            parameters={
                "type": "object",
                "properties": {"template": {"type": "string"}},
                "required": ["template"],
                "additionalProperties": False,
            },
            run=banner_get_template,
        ),
        Tool(
            name="banner_save_template",
            description=(
                "Create or update a reusable HTML/CSS/meta banner template inside the "
                "managed banner workspace after user confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "template": {"type": "string"},
                    "html": {"type": "string"},
                    "css": {"type": "string"},
                    "meta": {"type": "object"},
                    "overwrite": {"type": "boolean"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["template", "html", "css", "meta", "confirmed"],
                "additionalProperties": False,
            },
            run=banner_save_template,
        ),
        Tool(
            name="banner_render",
            description=(
                "Render a reusable banner with data through Playwright and return a "
                "managed PNG path plus Telegram review readiness."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "template": {"type": "string"},
                    "data": {"type": "object"},
                    "size": {
                        "type": "object",
                        "properties": {
                            "preset": {
                                "type": "string",
                                "enum": ["x_landscape", "square", "story", "custom"],
                            },
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "output_slug": {"type": "string"},
                },
                "additionalProperties": False,
            },
            run=banner_render,
        ),
    )
