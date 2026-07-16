"""Background memory review tests."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from agent.memory.background_review import spawn_memory_review
from agent.memory.config import MemoryConfig
from agent.memory.store import MemoryStore
from agent.messages import Completion
from agent.providers.base import Provider
from agent.session import AgentSession
from agent.prompts import PromptBuilder


class ReviewFakeProvider(Provider):
    name = "fake"
    _review_handled = False

    def complete(self, messages, model, options=None):
        is_review = any(
            message.get("role") == "user"
            and isinstance(message.get("content"), str)
            and "Review the conversation" in message["content"]
            for message in messages
        )
        if is_review and not self._review_handled:
            self._review_handled = True
            return Completion(
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "memory",
                                "arguments": json.dumps(
                                    {
                                        "action": "add",
                                        "target": "user",
                                        "content": "Likes terse answers",
                                    }
                                ),
                            },
                        }
                    ],
                }
            )
        return Completion(message={"role": "assistant", "content": "Nothing to save."})


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "akvan"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_spawn_memory_review_writes_user_profile(akvan_home: Path) -> None:
    store = MemoryStore(user_char_limit=500)
    store.load_from_disk()
    config = MemoryConfig(nudge_interval=1)
    done = threading.Event()
    result: list[str | None] = []

    def on_complete(message: str | None) -> None:
        result.append(message)
        done.set()

    spawn_memory_review(
        provider=ReviewFakeProvider(),
        model="model",
        memory_store=store,
        memory_config=config,
        messages_snapshot=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "keep it short please"},
            {"role": "assistant", "content": "sure"},
        ],
        on_complete=on_complete,
    )
    assert done.wait(timeout=5)
    store.load_from_disk()
    assert any("terse" in entry for entry in store.user_entries)


def test_begin_turn_sets_review_pending(akvan_home: Path) -> None:
    home = akvan_home
    project = akvan_home.parent / "project"
    project.mkdir()
    session = AgentSession.create(
        provider=ReviewFakeProvider(),
        model="model",
        max_iterations=3,
        prompt_builder=PromptBuilder(cwd=project, user_home=home),
        store=None,
    )
    session.prompt.memory_config = MemoryConfig(nudge_interval=2)
    session.begin_turn()
    assert session._turns_since_memory == 1
    assert session._memory_review_pending is False
    session.begin_turn()
    assert session._memory_review_pending is True
