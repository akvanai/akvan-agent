"""Gateway session reset tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.gateway.bindings import cache_key, reset_session
from agent.session import AgentSession
from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path) -> SessionStore:
    db = SessionStore(db_path=tmp_path / "state.db")
    yield db
    db.close()


def test_reset_session_clears_binding_and_cache(
    store: SessionStore,
    monkeypatch,
) -> None:
    created_ids: list[str] = []

    def factory(*, session_id: str | None = None) -> AgentSession:
        sid = session_id or "generated"
        created_ids.append(sid)
        session = MagicMock(spec=AgentSession)
        persistence = MagicMock()
        persistence.session_id = sid
        persistence.session_source = "telegram"
        session.persistence = persistence
        session.model = "test"
        provider = MagicMock()
        provider.name = "fake"
        session.provider = provider
        prompt = MagicMock()
        prompt.builder = MagicMock(cwd="/tmp")
        session.prompt = prompt
        session.end = MagicMock()
        return session

    chat_id = "12345"
    first = factory()
    store.create_session(first.persistence.session_id, source="telegram")
    store.set_gateway_binding("telegram", chat_id, first.persistence.session_id)
    cache = {cache_key("telegram", chat_id): first}

    second = reset_session(
        platform="telegram",
        chat_id=chat_id,
        store=store,
        session_cache=cache,
        factory=factory,
    )

    first.end.assert_called_once()
    assert store.get_gateway_binding("telegram", chat_id) == second.persistence.session_id
    assert cache[cache_key("telegram", chat_id)] is second
    assert second.persistence.session_id != first.persistence.session_id
