"""SessionStore persistence tests."""

from __future__ import annotations

import time

import pytest

from agent.messages import Message
from agent.storage.store import SessionStore


@pytest.fixture
def store(tmp_path) -> SessionStore:
    db = SessionStore(db_path=tmp_path / "state.db")
    yield db
    db.close()


def test_create_and_round_trip_messages(store: SessionStore) -> None:
    store.create_session(
        "sess-1",
        source="cli",
        model="test-model",
        provider="fake",
        cwd="/tmp/project",
    )
    messages: list[Message] = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "tool_name": "terminal", "content": "ok"},
    ]
    for message in messages:
        store.append_message("sess-1", message)

    loaded = store.get_messages("sess-1")
    assert loaded == messages


def test_sync_messages_only_writes_new_rows(store: SessionStore) -> None:
    store.create_session("sess-2", source="cli")
    messages: list[Message] = [
        {"role": "system", "content": "ignored"},
        {"role": "user", "content": "first"},
    ]
    written = store.sync_messages("sess-2", messages, start_index=0)
    assert written == 1

    written_again = store.sync_messages("sess-2", messages, start_index=2)
    assert written_again == 0

    messages.append({"role": "assistant", "content": "second"})
    written_third = store.sync_messages("sess-2", messages, start_index=2)
    assert written_third == 1
    assert len(store.get_messages("sess-2")) == 2


def test_replace_messages_is_atomic_and_preserves_compaction_summary(store: SessionStore) -> None:
    store.create_session("sess-compact", source="cli")
    store.append_message("sess-compact", {"role": "user", "content": "old"})
    compacted: list[Message] = [
        {"role": "system", "content": "live prompt is not persisted"},
        {"role": "assistant", "content": "[CONTEXT COMPACTION] summary", "_compressed_summary": True},
        {"role": "user", "content": "latest"},
    ]
    assert store.replace_messages("sess-compact", compacted) == 2
    assert store.get_messages("sess-compact") == [
        {"role": "assistant", "content": "[CONTEXT COMPACTION] summary"},
        {"role": "user", "content": "latest"},
    ]


def test_list_sessions_includes_preview_and_title(store: SessionStore) -> None:
    store.create_session("aaa-111", source="cli")
    store.append_message("aaa-111", {"role": "user", "content": "Fix auth bug"})

    time.sleep(0.01)
    store.create_session("bbb-222", source="cli")
    store.append_message("bbb-222", {"role": "user", "content": "Deploy docker"})

    rows = store.list_sessions(limit=10)
    assert [row["id"] for row in rows] == ["bbb-222", "aaa-111"]
    assert rows[1]["title"] == "Fix auth bug"
    assert rows[1]["preview"] == "Fix auth bug"


def test_resolve_session_id_by_prefix_and_title(store: SessionStore) -> None:
    store.create_session("abcd-1234", source="cli")
    store.append_message("abcd-1234", {"role": "user", "content": "My unique title"})

    assert store.resolve_session_id("abcd-1234") == "abcd-1234"
    assert store.resolve_session_id("abcd") == "abcd-1234"
    assert store.resolve_session_id("My unique title") == "abcd-1234"
    assert store.resolve_session_id("missing") is None


def test_end_session_sets_ended_at(store: SessionStore) -> None:
    store.create_session("sess-end", source="cli")
    store.end_session("sess-end")

    rows = store.list_sessions()
    assert rows[0]["ended_at"] is not None

    before = rows[0]["ended_at"]
    store.end_session("sess-end")
    rows_after = store.list_sessions()
    assert rows_after[0]["ended_at"] == before


def test_list_sessions_pagination_and_count(store: SessionStore) -> None:
    from agent.storage.store import SESSION_PAGE_SIZE

    for index in range(SESSION_PAGE_SIZE + 3):
        store.create_session(f"page-{index}", source="cli")

    assert store.count_sessions() == SESSION_PAGE_SIZE + 3
    page_one = store.list_sessions(limit=SESSION_PAGE_SIZE, offset=0)
    page_two = store.list_sessions(limit=SESSION_PAGE_SIZE, offset=SESSION_PAGE_SIZE)
    assert len(page_one) == SESSION_PAGE_SIZE
    assert len(page_two) == 3
