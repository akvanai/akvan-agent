"""Vision tools and telegram inbound photo handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools.vision_tools import build_vision_tools
from agent.vision.encode import write_png_bytes


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_vision_analyze_attaches_local_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKVAN_HOME", str(tmp_path / "home"))
    path = write_png_bytes(TINY_PNG, prefix="analyze")
    tool = build_vision_tools()[0]
    result = tool.invoke({"image": str(path), "question": "what?"})
    assert result.images
    assert result.images[0].path == str(path.resolve())
    assert '"ok": true' in result.content.lower() or '"ok": true' in result.content


def test_inbound_photo_only_uses_default_caption() -> None:
    telegram = pytest.importorskip("telegram")
    from agent.gateway.integrations.telegram.adapter import inbound_from_update

    class _User:
        id = 1
        full_name = "U"
        username = "u"

    class _Chat:
        id = 2
        type = telegram.constants.ChatType.PRIVATE

    class _Message:
        message_id = 3
        text = None
        caption = None

    class _Update:
        effective_message = _Message()
        effective_chat = _Chat()
        effective_user = _User()

    inbound = inbound_from_update(
        _Update(),
        image_paths=("/tmp/photo.jpg",),
    )
    assert inbound is not None
    assert inbound.text == "Please examine this image."
    assert inbound.image_paths == ("/tmp/photo.jpg",)


def test_compaction_prunes_old_tool_images() -> None:
    from agent.context.budget import ContextBudget
    from agent.context.compression import ContextCompressor
    from agent.context.config import ContextConfig

    config = ContextConfig(
        context_length=16_000,
        compression_threshold=0.50,
        protect_first_messages=1,
        protect_recent_ratio=0.20,
        summary_max_chars=4_000,
    )
    compressor = ContextCompressor(config, ContextBudget.for_model("tiny", config))
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
        {
            "role": "tool",
            "name": "browser_vision",
            "tool_call_id": "c1",
            "content": [
                {"type": "text", "text": "shot " + ("x" * 500)},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
            ],
        },
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "LATEST"},
    ]
    pruned, count = compressor.prune_old_tool_results(messages, protected_start=4)
    assert count >= 1
    tool = pruned[2]
    content = tool["content"]
    if isinstance(content, list):
        assert all(part.get("type") != "image_url" for part in content)
    else:
        assert "image omitted" in str(content) or "Historical" in str(content)
