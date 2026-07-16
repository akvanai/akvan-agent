"""MemoryStore tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.memory.paths import memory_file, user_file
from agent.memory.store import MemoryStore
from agent.tools.memory_tools import memory_tool


@pytest.fixture
def akvan_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "akvan"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_add_and_frozen_snapshot(akvan_home: Path) -> None:
    store = MemoryStore(memory_char_limit=500, user_char_limit=200)
    store.load_from_disk()
    result = store.add("memory", "User prefers concise replies")
    assert result["success"] is True
    assert memory_file().exists()

    store.load_from_disk()
    block = store.format_for_system_prompt("memory")
    assert block is not None
    assert "User prefers concise replies" in block

    store.add("user", "Name is Alex")
    assert user_file().exists()


def test_replace_and_remove(akvan_home: Path) -> None:
    store = MemoryStore(memory_char_limit=500, user_char_limit=200)
    store.load_from_disk()
    store.add("memory", "Project uses Python 3.12")
    store.replace("memory", "Python 3.12", "Project uses Python 3.13")
    store.remove("memory", "Python 3.13")
    assert store._char_count("memory") == 0


def test_batch_operations(akvan_home: Path) -> None:
    store = MemoryStore(memory_char_limit=500, user_char_limit=200)
    store.load_from_disk()
    store.add("memory", "old fact one")
    store.add("memory", "old fact two")
    result = store.apply_batch(
        "memory",
        [
            {"action": "remove", "old_text": "old fact one"},
            {"action": "add", "content": "merged fact"},
        ],
    )
    assert result["success"] is True
    assert "merged fact" in store.memory_entries
    assert "old fact one" not in store.memory_entries


def test_memory_tool_handler(akvan_home: Path) -> None:
    store = MemoryStore(memory_char_limit=500, user_char_limit=200)
    store.load_from_disk()
    raw = memory_tool(
        action="add",
        target="user",
        content="Prefers dark mode",
        store=store,
    )
    payload = json.loads(raw)
    assert payload["success"] is True
