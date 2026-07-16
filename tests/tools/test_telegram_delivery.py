"""Telegram delivery tool tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent.config import resolve_enabled_toolsets
from agent.tools.telegram_delivery import build_telegram_delivery_tools
from agent.tools.telegram_delivery.config import TelegramDeliverySettings


def _tools(tmp_path: Path):
    return {tool.name: tool for tool in build_telegram_delivery_tools(project_root=tmp_path)}


def _settings(*users: str) -> TelegramDeliverySettings:
    return TelegramDeliverySettings("secret-token", frozenset(users), source="explicit")


def test_telegram_toolset_enabled_when_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    assert "telegram_delivery" in resolve_enabled_toolsets(project_root=tmp_path)


def test_telegram_toolset_enabled_with_delivery_keys(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_DELIVERY_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_DELIVERY_ALLOWED_USERS", "42")
    assert "telegram_delivery" in resolve_enabled_toolsets(project_root=tmp_path)


def test_tools_include_file_text_and_image(tmp_path: Path) -> None:
    assert set(_tools(tmp_path)) == {
        "telegram_send_file",
        "telegram_send_text",
        "telegram_send_image",
    }


def test_send_file_requires_explicit_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="explicit user confirmation"):
        _tools(tmp_path)["telegram_send_file"].invoke({
            "file_path": str(path), "confirmed": False,
        })


def test_send_file_uses_only_authorized_recipient(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )
    with pytest.raises(ValueError, match="not in the Telegram delivery allowlist"):
        _tools(tmp_path)["telegram_send_file"].invoke({
            "file_path": str(path), "recipient_user_id": "99", "confirmed": True,
        })


def test_send_image_posts_photo(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "banner.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )

    def fake_post(url: str, **kwargs):
        assert url.endswith("/sendPhoto")
        assert kwargs["data"] == {"chat_id": "42", "caption": "Draft one"}
        assert kwargs["files"]["photo"][0] == "banner.png"
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 17}},
            request=httpx.Request("POST", "https://api.telegram.test/sendPhoto"),
        )

    monkeypatch.setattr("agent.tools.telegram_delivery.httpx.post", fake_post)
    rendered = _tools(tmp_path)["telegram_send_file"].invoke({
        "file_path": str(image), "caption": "Draft one", "confirmed": True,
    })
    payload = json.loads(rendered.content)
    assert payload["delivered"] is True
    assert payload["method"] == "sendPhoto"
    assert payload["recipient_user_id"] == "42"
    assert payload["message_id"] == 17


def test_send_pdf_posts_document(monkeypatch, tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )

    def fake_post(url: str, **kwargs):
        assert url.endswith("/sendDocument")
        assert kwargs["files"]["document"][0] == "report.pdf"
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 9}},
            request=httpx.Request("POST", "https://api.telegram.test/sendDocument"),
        )

    monkeypatch.setattr("agent.tools.telegram_delivery.httpx.post", fake_post)
    rendered = _tools(tmp_path)["telegram_send_file"].invoke({
        "file_path": str(pdf), "confirmed": True,
    })
    payload = json.loads(rendered.content)
    assert payload["method"] == "sendDocument"
    assert payload["content_type"] == "application/pdf"


def test_send_audio_posts_audio(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"ID3")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )

    def fake_post(url: str, **kwargs):
        assert url.endswith("/sendAudio")
        assert kwargs["files"]["audio"][0] == "clip.mp3"
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 3}},
            request=httpx.Request("POST", "https://api.telegram.test/sendAudio"),
        )

    monkeypatch.setattr("agent.tools.telegram_delivery.httpx.post", fake_post)
    rendered = _tools(tmp_path)["telegram_send_file"].invoke({
        "file_path": str(audio), "confirmed": True,
    })
    assert json.loads(rendered.content)["method"] == "sendAudio"


def test_send_file_rejects_oversize(monkeypatch, tmp_path: Path) -> None:
    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"xx")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )
    monkeypatch.setattr("agent.tools.telegram_delivery._MAX_UPLOAD_BYTES", 1)
    with pytest.raises(ValueError, match="too large"):
        _tools(tmp_path)["telegram_send_file"].invoke({
            "file_path": str(huge), "confirmed": True,
        })


def test_send_text_posts_message(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )

    def fake_post(url: str, **kwargs):
        assert url.endswith("/sendMessage")
        assert kwargs["data"] == {"chat_id": "42", "text": "Hello from Akvan"}
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 5}},
            request=httpx.Request("POST", "https://api.telegram.test/sendMessage"),
        )

    monkeypatch.setattr("agent.tools.telegram_delivery.httpx.post", fake_post)
    rendered = _tools(tmp_path)["telegram_send_text"].invoke({
        "text": "Hello from Akvan", "confirmed": True,
    })
    payload = json.loads(rendered.content)
    assert payload["method"] == "sendMessage"
    assert payload["message_id"] == 5


def test_send_text_requires_confirmation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit user confirmation"):
        _tools(tmp_path)["telegram_send_text"].invoke({
            "text": "hi", "confirmed": False,
        })


def test_send_text_rejects_empty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )
    with pytest.raises(ValueError, match="must not be empty"):
        _tools(tmp_path)["telegram_send_text"].invoke({
            "text": "   ", "confirmed": True,
        })


def test_send_text_rejects_overlong(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )
    with pytest.raises(ValueError, match="too long"):
        _tools(tmp_path)["telegram_send_text"].invoke({
            "text": "x" * 4097, "confirmed": True,
        })


def test_send_image_delegate_still_works(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "banner.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(
        "agent.tools.telegram_delivery.load_telegram_delivery_settings",
        lambda: _settings("42"),
    )

    def fake_post(url: str, **kwargs):
        assert url.endswith("/sendPhoto")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=httpx.Request("POST", "https://api.telegram.test/sendPhoto"),
        )

    monkeypatch.setattr("agent.tools.telegram_delivery.httpx.post", fake_post)
    rendered = _tools(tmp_path)["telegram_send_image"].invoke({
        "image_path": str(image), "confirmed": True,
    })
    assert json.loads(rendered.content)["delivered"] is True
