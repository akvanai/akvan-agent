"""Gateway binding persistence tests."""

from __future__ import annotations

import pytest

from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path) -> SessionStore:
    db = SessionStore(db_path=tmp_path / "state.db")
    yield db
    db.close()


def test_gateway_binding_round_trip(store: SessionStore) -> None:
    store.create_session("sess-a", source="telegram")
    store.set_gateway_binding("telegram", "12345", "sess-a")
    assert store.get_gateway_binding("telegram", "12345") == "sess-a"


def test_gateway_binding_update(store: SessionStore) -> None:
    store.create_session("sess-a", source="telegram")
    store.create_session("sess-b", source="telegram")
    store.set_gateway_binding("telegram", "999", "sess-a")
    store.set_gateway_binding("telegram", "999", "sess-b")
    assert store.get_gateway_binding("telegram", "999") == "sess-b"


def test_gateway_binding_clear(store: SessionStore) -> None:
    store.create_session("sess-a", source="telegram")
    store.set_gateway_binding("telegram", "42", "sess-a")
    store.clear_gateway_binding("telegram", "42")
    assert store.get_gateway_binding("telegram", "42") is None


def test_schema_migration_adds_gateway_tables(tmp_path) -> None:
    store = SessionStore(db_path=tmp_path / "fresh.db")
    with store._lock:
        version = store._conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        table = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'gateway_bindings'"
        ).fetchone()
        preferences = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'gateway_preferences'"
        ).fetchone()
    assert int(version["version"]) == 3
    assert table is not None
    assert preferences is not None
    store.close()


def test_gateway_preferences_round_trip_and_partial_update(store: SessionStore) -> None:
    assert store.get_gateway_preferences("telegram", "chat") == {}

    store.set_gateway_preferences(
        "telegram", "chat", model="model-a", approval_mode="ask"
    )
    store.set_gateway_preferences(
        "telegram", "chat", stream_transport="draft"
    )

    assert store.get_gateway_preferences("telegram", "chat") == {
        "model": "model-a",
        "approval_mode": "ask",
        "stream_transport": "draft",
    }
