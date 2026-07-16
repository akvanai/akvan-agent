"""FTS5 session search tests."""

from __future__ import annotations

import json
import threading

import pytest

from agent.storage.store import SessionStore
from agent.tools.session_search_tools import SessionSearchContext, session_search


@pytest.fixture
def store(tmp_path) -> SessionStore:
    db = SessionStore(db_path=tmp_path / "state.db")
    yield db
    db.close()


def test_fts_search_and_scroll(store: SessionStore) -> None:
    store.create_session("sess-a", source="cli")
    store.append_message("sess-a", {"role": "user", "content": "deploy kubernetes cluster"})
    mid = store.append_message(
        "sess-a",
        {"role": "assistant", "content": "Here is the deployment plan"},
    )
    store.append_message("sess-a", {"role": "user", "content": "thanks"})

    hits = store.search_messages("kubernetes")
    assert len(hits) >= 1
    assert hits[0]["session_id"] == "sess-a"

    around = store.get_messages_around("sess-a", mid, window=1)
    assert any(row.get("anchor") for row in around)

    ctx = SessionSearchContext(store=store, current_session_id=lambda: "other")
    discover = json.loads(session_search(query="kubernetes", ctx=ctx))
    assert discover["success"] is True
    assert discover["mode"] == "discover"
    assert discover["count"] >= 1

    scroll = json.loads(
        session_search(
            session_id="sess-a",
            around_message_id=mid,
            ctx=ctx,
        )
    )
    assert scroll["success"] is True
    assert scroll["mode"] == "scroll"

    browse = json.loads(session_search(ctx=ctx))
    assert browse["success"] is True
    assert browse["mode"] == "browse"


def test_fts_search_from_background_thread(store: SessionStore) -> None:
    store.create_session("sess-thread", source="cli")
    store.append_message(
        "sess-thread",
        {"role": "user", "content": "akvan agent memory design"},
    )
    ctx = SessionSearchContext(store=store, current_session_id=lambda: "other")
    result: dict[str, object] = {}

    def worker() -> None:
        payload = json.loads(session_search(query="akvan", ctx=ctx))
        result.update(payload)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert result.get("success") is True
    assert result.get("mode") == "discover"
