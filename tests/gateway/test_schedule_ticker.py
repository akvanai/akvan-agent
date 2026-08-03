"""Tests for gateway schedule ticker lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config import Settings
from agent.gateway.config import GatewayRuntimeConfig
from agent.gateway.service import GatewayService
from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    home = tmp_path / ".akvan"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AKVAN_HOME", str(home))
    db = SessionStore(db_path=home / "state.db")
    yield db
    db.close()


def _settings() -> Settings:
    return Settings(
        provider="openrouter",
        model="openai/gpt-4o-mini",
        openrouter_api_key="test-key",
    )


def test_gateway_starts_and_stops_schedule_ticker(store: SessionStore) -> None:
    adapter = MagicMock()
    adapter.capabilities.callbacks = False
    adapter.connect = AsyncMock(return_value=True)
    adapter.disconnect = AsyncMock()
    adapter.set_message_handler = MagicMock()
    adapter.set_callback_handler = MagicMock()

    provider = MagicMock()
    provider.name = "openrouter"
    provider.close = MagicMock()

    service = GatewayService(
        settings=_settings(),
        gateway_id="telegram",
        gateway_name="Telegram",
        runtime_config=GatewayRuntimeConfig(),
        access_policy=lambda _uid: True,
        provider=provider,
        store=store,
        adapter=adapter,
    )

    tick_calls: list[int] = []

    def fake_tick(*_args, **_kwargs) -> int:
        tick_calls.append(1)
        return 0

    async def scenario() -> None:
        with (
            patch("agent.gateway.service.is_schedule_enabled", return_value=True),
            patch(
                "agent.gateway.service.load_schedule_config",
                return_value=MagicMock(tick_interval_seconds=0.05),
            ),
            patch("agent.gateway.service.schedule_tick", side_effect=fake_tick),
        ):
            await service.start()
            assert service._schedule_task is not None
            await asyncio.sleep(0.12)
            await service.stop()

    asyncio.run(scenario())
    assert service._schedule_task is None
    assert len(tick_calls) >= 1
    adapter.connect.assert_awaited()
    adapter.disconnect.assert_awaited()
