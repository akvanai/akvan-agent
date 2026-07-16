from __future__ import annotations

import json
import threading
from pathlib import Path

from agent.knowledge.config import KnowledgeConfig
from agent.knowledge.review import (
    persisted_review_batch,
    read_review_state,
    spawn_knowledge_review,
)
from agent.messages import Completion
from agent.providers.base import Provider
from agent.knowledge.store import KnowledgeStore
from agent.storage.store import SessionStore
from agent.tools.base import ToolResultKind
from agent.tools.knowledge_tools import build_knowledge_tools


def test_knowledge_reads_are_untrusted(tmp_path: Path) -> None:
    store = KnowledgeStore(root=tmp_path / "knowledge", state_root=tmp_path / "state")
    tools = {tool.name: tool for tool in build_knowledge_tools(store)}
    result = tools["knowledge_search"].invoke({"query": "anything"})
    assert result.kind == ToolResultKind.UNTRUSTED_DATA


def test_persisted_review_batch_uses_exact_user_turn_limit(tmp_path: Path) -> None:
    db = SessionStore(db_path=tmp_path / "state.db")
    try:
        db.create_session("s", source="cli")
        for index in range(4):
            db.append_message("s", {"role": "user", "content": f"user {index}"})
            db.append_message("s", {"role": "assistant", "content": f"answer {index}"})
        knowledge = KnowledgeStore(
            KnowledgeConfig(review_interval=3),
            root=tmp_path / "knowledge",
            state_root=tmp_path / "knowledge-state",
        )
        batch = persisted_review_batch(db, knowledge)
        assert batch is not None
        high_water, messages = batch
        assert high_water > 0
        assert sum(message["role"] == "user" for message in messages) == 3
        assert [message["content"] for message in messages if message["role"] == "user"] == [
            "user 0",
            "user 1",
            "user 2",
        ]
    finally:
        db.close()


def test_propose_tool_verifies_user_quote(tmp_path: Path) -> None:
    store = KnowledgeStore(root=tmp_path / "knowledge", state_root=tmp_path / "state")
    tools = {
        tool.name: tool
        for tool in build_knowledge_tools(
            store,
            user_messages=lambda: ["Our official color is orange."],
        )
    }
    result = tools["knowledge_propose"].invoke(
        {
            "operation": "create",
            "concept_id": "brand/color",
            "frontmatter": {
                "type": "Brand Identity",
                "title": "Brand color",
                "description": "Official color.",
            },
            "body": "# Color\n\nOrange.",
            "evidence": [{"kind": "explicit_user", "quote": "official color is orange"}],
            "confidence": "high",
        }
    )
    assert json.loads(result.content)["status"] == "applied"


class KnowledgeReviewProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.called = False

    def complete(self, messages, model, options=None):
        if not self.called:
            self.called = True
            return Completion(
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "knowledge-1",
                            "type": "function",
                            "function": {
                                "name": "knowledge_propose",
                                "arguments": json.dumps(
                                    {
                                        "operation": "create",
                                        "concept_id": "brand/identity",
                                        "frontmatter": {
                                            "type": "Brand Identity",
                                            "title": "Brand identity",
                                            "description": "Official brand details.",
                                        },
                                        "body": "# Brand identity\n\nAccent: `#FF9F1C`.",
                                        "evidence": [
                                            {
                                                "kind": "explicit_user",
                                                "quote": "official brand accent is #FF9F1C",
                                            }
                                        ],
                                        "confidence": "high",
                                    }
                                ),
                            },
                        }
                    ],
                }
            )
        return Completion(message={"role": "assistant", "content": "Done."})


def test_curator_applies_fact_advances_cursor_and_notifies(tmp_path: Path) -> None:
    store = KnowledgeStore(root=tmp_path / "knowledge", state_root=tmp_path / "state")
    done = threading.Event()
    notifications: list[str | None] = []

    def on_complete(message: str | None) -> None:
        notifications.append(message)
        done.set()

    spawn_knowledge_review(
        provider=KnowledgeReviewProvider(),
        model="test-model",
        knowledge_store=store,
        messages_snapshot=[
            {"role": "user", "content": "Our official brand accent is #FF9F1C."},
            {"role": "assistant", "content": "Understood."},
        ],
        high_water_message_id=42,
        on_complete=on_complete,
    )

    assert done.wait(timeout=5)
    assert store.read("brand/identity")["frontmatter"]["title"] == "Brand identity"
    assert read_review_state(store.state_root)["last_message_id"] == 42
    assert notifications == ["Knowledge: 1 automatic update(s)"]
