import json

from agent.context.budget import ContextBudget
from agent.context.compression import SUMMARY_MARKER, ContextCompressor
from agent.context.config import ContextConfig


def compressor() -> ContextCompressor:
    config = ContextConfig(
        context_length=16_000,
        compression_threshold=0.50,
        protect_first_messages=1,
        protect_recent_ratio=0.20,
        summary_max_chars=4_000,
    )
    return ContextCompressor(config, ContextBudget.for_model("tiny", config))


def test_compaction_preserves_first_and_latest_request_and_prunes_tools() -> None:
    old = "template-id=story-card path=/tmp/card " + "x" * 12_000
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "banner", "arguments": json.dumps({"template": "z" * 2_000})}}]},
        {"role": "tool", "name": "banner", "tool_call_id": "c1", "content": old},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "LATEST REQUEST"},
    ]
    result = compressor().compact(messages, force=True)
    assert result.changed
    assert result.messages[0] == messages[0]
    assert result.messages[-1] == messages[-1]
    summary = next(message for message in result.messages if message.get("_compressed_summary"))
    assert summary["role"] == "assistant"
    assert SUMMARY_MARKER in str(summary["content"])
    assert "template-id=story-card" in str(summary["content"])
    assert result.after_tokens < result.before_tokens


def test_recompaction_does_not_nest_summary_markers() -> None:
    messages = [{"role": "system", "content": "system"}]
    for index in range(12):
        messages.extend((
            {"role": "user", "content": f"request {index} " + "x" * 800},
            {"role": "assistant", "content": f"answer {index} " + "y" * 800},
        ))
    first = compressor().compact(messages, force=True)
    second = compressor().compact(first.messages, force=True)
    combined = "\n".join(str(message.get("content") or "") for message in second.messages)
    assert combined.count(SUMMARY_MARKER) == 1
