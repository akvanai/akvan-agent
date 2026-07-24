"""OpenAI Codex provider auth, request, and streaming tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent.config import Settings
from agent.providers import build_provider
from agent.providers.base import ProviderError
from agent.providers.openai_codex import (
    CODEX_CLIENT_VERSION,
    DEFAULT_CODEX_MODELS,
    OpenAICodexProvider,
    codex_auth_candidate_paths,
    load_codex_cli_token,
)


def test_openai_codex_api_key_request_construction() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "done"}}
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICodexProvider(api_key="openai-key", client=client)

    completion = provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5-codex",
        options={"tools": []},
    )

    assert completion.message == {"role": "assistant", "content": "done"}
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer openai-key"
    assert request.headers["Content-Type"] == "application/json"
    assert request.read() == (
        b'{"model":"gpt-5-codex","messages":[{"role":"user","content":"hello"}],"tools":[]}'
    )


def test_openai_codex_streams_text_and_tool_deltas() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"tool_calls":['
                    b'{"index":0,"id":"call_1","type":"function",'
                    b'"function":{"name":"testy","arguments":"{}"}}]}}]}\n\n'
                    b'data: [DONE]\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    provider = OpenAICodexProvider(api_key="openai-key", client=client)

    events = list(
        provider.stream_events(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-codex",
        )
    )

    assert events[0].content == "hel"
    assert events[1].tool_calls[0]["id"] == "call_1"


def test_openai_codex_cli_mode_uses_responses_stream() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                b'event: response.output_text.delta\n'
                b'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
                b'event: response.completed\n'
                b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICodexProvider(api_key="cli-token", auth_mode="cli", client=client)

    events = list(
        provider.stream_events(
            messages=[
                {"role": "system", "content": "You are Akvan."},
                {"role": "user", "content": "hello"},
            ],
            model="gpt-5.5",
            options={
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "testy",
                            "description": "Echo",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
            },
        )
    )

    assert events[0].content == "Hi"
    request = requests[0]
    assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
    body = json.loads(request.content)
    assert body["stream"] is True
    assert body["store"] is False
    assert body["instructions"] == "You are Akvan."
    assert body["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
    ]
    assert body["tools"][0]["name"] == "testy"


def test_openai_codex_cli_mode_parses_responses_function_call() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=(
                    b'event: response.output_item.done\n'
                    b'data: {"type":"response.output_item.done","output_index":0,'
                    b'"item":{"type":"function_call","call_id":"call_1",'
                    b'"name":"testy","arguments":"{}"}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
    )
    provider = OpenAICodexProvider(api_key="cli-token", auth_mode="cli", client=client)

    events = list(provider.stream_events([{"role": "user", "content": "hello"}], "gpt-5.5"))

    assert events[0].tool_calls[0]["id"] == "call_1"
    assert events[0].tool_calls[0]["function"] == {"name": "testy", "arguments": "{}"}


def test_openai_codex_lists_cli_models_from_backend() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-5.5",
                        "display_name": "GPT-5.5",
                        "context_window": 400000,
                        "visibility": "list",
                    },
                    {
                        "slug": "gpt-hidden",
                        "display_name": "Hidden",
                        "context_window": 1000,
                        "visibility": "hidden",
                    },
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICodexProvider(api_key="cli-token", auth_mode="cli", client=client)

    models = provider.list_models()

    assert len(models) == 1
    assert models[0].id == "gpt-5.5"
    assert models[0].name == "GPT-5.5"
    assert models[0].context_length == 400000
    request = requests[0]
    assert request.url.path == "/backend-api/codex/models"
    assert request.url.params["client_version"] == CODEX_CLIENT_VERSION
    assert request.headers["Authorization"] == "Bearer cli-token"


def test_openai_codex_lists_api_key_models_filtered() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.5"},
                    {"id": "gpt-5.3-codex"},
                    {"id": "o3-mini"},
                    {"id": "o4-mini"},
                    {"id": "gpt-4o"},
                    {"id": "text-embedding-3-small"},
                    {"id": "dall-e-3"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICodexProvider(api_key="openai-key", client=client)

    model_ids = [model.id for model in provider.list_models()]

    assert model_ids == ["gpt-5.5", "gpt-5.3-codex", "o3-mini", "o4-mini"]
    assert str(requests[0].url) == "https://api.openai.com/v1/models"


def test_openai_codex_list_models_raises_on_empty_catalog() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        )
    )
    provider = OpenAICodexProvider(api_key="openai-key", client=client)

    with pytest.raises(ProviderError, match="empty model list"):
        provider.list_models()


def test_openai_codex_default_models_fallback_catalog() -> None:
    model_ids = [model.id for model in DEFAULT_CODEX_MODELS]
    assert "gpt-5.5" in model_ids
    assert "gpt-5.3-codex" in model_ids


def test_load_codex_cli_token_reads_codex_home_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "cli-token", "refresh_token": "refresh"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert load_codex_cli_token() == "cli-token"


def test_codex_auth_candidate_paths_include_compatibility_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert codex_auth_candidate_paths() == [
        tmp_path / "custom-codex" / "auth.json",
        tmp_path / ".codex" / "auth.json",
        tmp_path / "codex" / "auth.json",
    ]


def test_load_codex_cli_token_falls_back_to_dot_codex_when_codex_home_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_home = tmp_path / "missing-codex-home"
    dot_codex = tmp_path / ".codex"
    dot_codex.mkdir()
    (dot_codex / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "dot-token", "refresh_token": "refresh"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(missing_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert load_codex_cli_token() == "dot-token"


def test_load_codex_cli_token_falls_back_to_home_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_codex = tmp_path / "codex"
    home_codex.mkdir()
    (home_codex / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "home-token", "refresh_token": "refresh"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert load_codex_cli_token() == "home-token"


def test_load_codex_cli_token_requires_codex_tokens_shape(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"access_token": "top-level-token"}), encoding="utf-8")

    with pytest.raises(ProviderError, match="tokens"):
        load_codex_cli_token(auth_path)


def test_load_codex_cli_token_requires_refresh_token(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "cli-token"}}),
        encoding="utf-8",
    )

    with pytest.raises(ProviderError, match="refresh_token"):
        load_codex_cli_token(auth_path)


def test_build_provider_uses_codex_cli_session_token(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "cli-token", "refresh_token": "refresh"}}),
        encoding="utf-8",
    )
    settings = Settings(
        provider="openai-codex",
        model="gpt-5-codex",
        codex_auth_mode="cli",
        codex_cli_auth_path=str(auth_path),
    )

    provider = build_provider(settings)

    assert isinstance(provider, OpenAICodexProvider)
    assert provider.auth_mode == "cli"
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"


def test_build_provider_requires_openai_key_for_api_key_mode() -> None:
    settings = Settings(
        provider="openai-codex",
        model="gpt-5-codex",
        codex_auth_mode="api-key",
    )

    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        build_provider(settings)


def test_codex_cli_mode_reports_missing_session(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(ProviderError, match="Codex CLI session"):
        load_codex_cli_token(missing)


def test_openai_codex_stream_http_error_includes_detail() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"message": "context length exceeded"}},
            )
        )
    )
    provider = OpenAICodexProvider(api_key="openai-key", client=client)

    with pytest.raises(ProviderError, match="context length exceeded") as exc_info:
        list(
            provider.stream_events(
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-5-codex",
            )
        )

    assert "StreamClosed" not in str(exc_info.value)
    assert "HTTP 400" in str(exc_info.value)


def test_openai_codex_cli_stream_http_error_includes_detail() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"message": "invalid_request"}},
            )
        )
    )
    provider = OpenAICodexProvider(api_key="cli-token", auth_mode="cli", client=client)

    with pytest.raises(ProviderError, match="invalid_request") as exc_info:
        list(
            provider.stream_events(
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-5.5",
            )
        )

    assert "StreamClosed" not in str(exc_info.value)
    assert "HTTP 400" in str(exc_info.value)
