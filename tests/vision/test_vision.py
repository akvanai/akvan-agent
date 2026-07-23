"""Vision routing, encoding, and attach helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Settings
from agent.vision.attach import (
    build_multimodal_content,
    build_tool_message_content,
    extract_text_from_content,
    prune_image_parts,
)
from agent.vision.capabilities import decide_vision_mode, model_looks_vision_capable
from agent.vision.encode import load_image_as_data_url, sniff_mime, write_png_bytes
from agent.vision.user_images import extract_image_paths_from_text
from agent.tools.base import ToolImage


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def png_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home"))
    path = write_png_bytes(TINY_PNG, prefix="test")
    return path


def test_model_looks_vision_capable() -> None:
    assert model_looks_vision_capable("openai/gpt-4o")
    assert model_looks_vision_capable("google/gemini-2.5-flash")
    assert not model_looks_vision_capable("deepseek-chat")


def test_decide_vision_mode_auto_and_overrides() -> None:
    settings = Settings(
        provider="openrouter",
        model="openai/gpt-4o",
        vision_mode="auto",
    )
    assert decide_vision_mode("openrouter", "openai/gpt-4o", settings) == "native"
    assert decide_vision_mode("deepseek", "deepseek-chat", settings) == "aux"

    aux = Settings(provider="openrouter", model="openai/gpt-4o", vision_mode="aux")
    assert decide_vision_mode("openrouter", "openai/gpt-4o", aux) == "aux"

    forced = Settings(
        provider="openrouter",
        model="custom-model",
        vision_mode="auto",
        model_supports_vision=True,
    )
    assert decide_vision_mode("openrouter", "custom-model", forced) == "native"


def test_sniff_and_data_url(png_path: Path) -> None:
    assert sniff_mime(TINY_PNG) == "image/png"
    data_url = load_image_as_data_url(str(png_path))
    assert data_url.startswith("data:image/png;base64,")


def test_build_multimodal_and_tool_content_native(
    png_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKVAN_VISION_MODE", "native")
    settings = Settings(
        provider="openrouter",
        model="openai/gpt-4o",
        vision_mode="native",
    )
    parts = build_multimodal_content("hello", [str(png_path)])
    assert parts[0] == {"type": "text", "text": "hello"}
    assert parts[1]["type"] == "image_url"

    content = build_tool_message_content(
        "tool text",
        (ToolImage(path=str(png_path), question="what?"),),
        provider="openrouter",
        model="openai/gpt-4o",
        settings=settings,
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"


def test_build_tool_content_aux_describes(
    png_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        provider="deepseek",
        model="deepseek-chat",
        vision_mode="aux",
    )

    def fake_describe(source: str, *, question: str = "", settings=None, **kwargs) -> str:
        return f"described:{Path(source).name}:{question}"

    monkeypatch.setattr("agent.vision.attach.describe_image", fake_describe)
    content = build_tool_message_content(
        "tool text",
        (ToolImage(path=str(png_path), question="q"),),
        provider="deepseek",
        model="deepseek-chat",
        settings=settings,
    )
    assert isinstance(content, str)
    assert "tool text" in content
    assert "described:" in content


def test_prune_image_parts() -> None:
    content = [
        {"type": "text", "text": "keep"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    pruned = prune_image_parts(content)
    assert isinstance(pruned, list)
    assert pruned[0]["text"] == "keep"
    assert "image omitted" in str(pruned[1]["text"])


def test_extract_text_from_content() -> None:
    assert extract_text_from_content("plain") == "plain"
    assert (
        extract_text_from_content(
            [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {"url": "x"}}]
        )
        == "a"
    )


def test_extract_image_paths_from_text(png_path: Path) -> None:
    display, paths = extract_image_paths_from_text(f"look @{png_path}")
    assert paths == (str(png_path.resolve()),)
    assert "look" in display
    assert str(png_path) not in display or "@" not in display
