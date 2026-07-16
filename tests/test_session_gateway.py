"""AgentSession gateway persistence helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.session import AgentSession
from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path) -> SessionStore:
    db = SessionStore(db_path=tmp_path / "state.db")
    yield db
    db.close()


def test_load_persisted_restores_messages(store: SessionStore, monkeypatch) -> None:
    store.create_session("saved", source="telegram")
    store.append_message("saved", {"role": "user", "content": "hello"})
    store.append_message("saved", {"role": "assistant", "content": "hi"})

    provider = MagicMock()
    provider.name = "fake"
    monkeypatch.setattr(
        "agent.session.tooling.build_registry",
        lambda *args, **kwargs: MagicMock(resolve=lambda toolsets: ()),
    )
    monkeypatch.setattr(
        "agent.session.session.PromptBuilder",
        lambda: MagicMock(
            discover_skills=MagicMock(return_value=MagicMock()),
            build=MagicMock(
                return_value=MagicMock(content="system", skills=MagicMock(skills={}))
            ),
            cwd="/tmp",
            user_home=store._db_path.parent,
            project_root=store._db_path.parent,
        ),
    )

    session = AgentSession.create(
        provider=provider,
        model="test",
        max_iterations=3,
        store=store,
        session_source="telegram",
    )
    error = session.load_persisted("saved")
    assert error is None
    assert session.persistence.session_id == "saved"
    assert any(message.get("role") == "user" for message in session.messages)


def test_ensure_persisted_after_gateway_pre_create(store: SessionStore, monkeypatch) -> None:
    provider = MagicMock()
    provider.name = "fake"
    monkeypatch.setattr(
        "agent.session.tooling.build_registry",
        lambda *args, **kwargs: MagicMock(resolve=lambda toolsets: ()),
    )
    monkeypatch.setattr(
        "agent.session.session.PromptBuilder",
        lambda: MagicMock(
            discover_skills=MagicMock(return_value=MagicMock()),
            build=MagicMock(
                return_value=MagicMock(content="system", skills=MagicMock(skills={}))
            ),
            cwd="/tmp",
            user_home=store._db_path.parent,
            project_root=store._db_path.parent,
        ),
    )

    session = AgentSession.create(
        provider=provider,
        model="test",
        max_iterations=3,
        store=store,
        session_id="gateway-sess",
        session_source="telegram",
    )
    store.ensure_session_exists(
        session.persistence.session_id,
        source=session.persistence.session_source,
        model=session.model,
        provider=session.provider.name,
        cwd=str(session.prompt.builder.cwd),
    )
    session.messages.append({"role": "user", "content": "hello"})
    session.messages.append({"role": "assistant", "content": "hi"})
    session.persist_new_messages()
    assert store.get_messages(session.persistence.session_id) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
