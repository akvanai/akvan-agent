from pathlib import Path

from agent.context.budget import ContextBudget
from agent.context.config import ContextConfig
from agent.context.result_storage import PERSISTED_TAG, ToolResultStore
from agent.tools.base import ToolResult, ToolResultKind


def make_store(tmp_path: Path, **overrides) -> ToolResultStore:
    config = ContextConfig(
        context_length=20_000,
        max_result_chars=8_000,
        max_turn_chars=16_000,
        result_preview_chars=500,
        **overrides,
    )
    return ToolResultStore(
        tmp_path / "results",
        ContextBudget.for_model("tiny", config),
        config,
        session_id="../unsafe-session",
    )


def test_large_result_is_private_recoverable_and_preview_redacted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    secret = "api_key=sk-abcdefghijklmnopqrst"
    original = secret + "\n" + "x" * 9_000
    bounded = store.bound_result(
        ToolResult.trusted(original), tool_name="../banner", call_id="../call"
    )
    assert PERSISTED_TAG in bounded.content
    assert "sk-abcdefghijklmnopqrst" not in bounded.content
    assert "[REDACTED]" in bounded.content
    assert bounded.kind == ToolResultKind.TRUSTED_INSTRUCTIONS
    files = list((tmp_path / "results").rglob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == original
    assert files[0].stat().st_mode & 0o077 == 0
    assert files[0].is_relative_to(tmp_path / "results")


def test_aggregate_budget_persists_largest_results(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    messages = [
        {"role": "tool", "name": f"t{i}", "tool_call_id": f"c{i}", "content": char * 7_000}
        for i, char in enumerate("abc")
    ]
    store.enforce_turn_budget(messages, [0, 1, 2])
    assert sum(len(str(message["content"])) for message in messages) <= 16_000
    assert sum(PERSISTED_TAG in str(message["content"]) for message in messages) >= 1
    assert len(list((tmp_path / "results").rglob("*.txt"))) >= 1


def test_small_result_is_unchanged(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = ToolResult("small")
    assert store.bound_result(result, tool_name="tool", call_id="call") is result
