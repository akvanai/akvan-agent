"""Codex CLI Responses API multimodal mapping."""

from __future__ import annotations

from agent.providers.openai_codex import _responses_payload


def test_responses_payload_maps_tool_images_to_input_image() -> None:
    data_url = "data:image/png;base64,AAAA"
    payload = _responses_payload(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "browser_vision", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "browser_vision",
                "content": [
                    {"type": "text", "text": "screenshot ok"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        model="gpt-5.5",
    )
    items = payload["input"]
    assert any(i.get("type") == "function_call" for i in items)
    output = next(i for i in items if i.get("type") == "function_call_output")
    assert output["output"] == "screenshot ok"
    image_msg = next(
        i
        for i in items
        if i.get("role") == "user"
        and isinstance(i.get("content"), list)
        and any(
            isinstance(p, dict) and p.get("type") == "input_image" for p in i["content"]
        )
    )
    assert {"type": "input_image", "image_url": data_url} in image_msg["content"]


def test_responses_payload_maps_user_multimodal() -> None:
    data_url = "data:image/png;base64,BBBB"
    payload = _responses_payload(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        model="gpt-5.5",
    )
    content = payload["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "what is this?"}
    assert content[1] == {"type": "input_image", "image_url": data_url}
