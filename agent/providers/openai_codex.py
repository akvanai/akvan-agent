"""OpenAI Codex provider and Codex CLI-session token loading."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import httpx

from agent.messages import Completion, Message
from agent.providers.base import (
    ModelInfo,
    Provider,
    ProviderError,
    ProviderStreamEvent,
)

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

DEFAULT_CODEX_MODELS = (
    ModelInfo("gpt-5.5", "GPT-5.5"),
    ModelInfo("gpt-5.4-mini", "GPT-5.4 Mini"),
    ModelInfo("gpt-5.4", "GPT-5.4"),
    ModelInfo("gpt-5.3-codex", "GPT-5.3 Codex"),
    ModelInfo("gpt-5.3-codex-spark", "GPT-5.3 Codex Spark"),
)


class OpenAICodexProvider(Provider):
    """OpenAI-compatible provider for Codex models."""

    name = "openai-codex"
    base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        *,
        auth_mode: str = "api-key",
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not api_key.strip():
            credential_name = "Codex CLI session" if auth_mode == "cli" else "OPENAI_API_KEY"
            raise ProviderError(f"{credential_name} is required for OpenAI Codex.")
        self._api_key = api_key
        self.auth_mode = auth_mode
        self.base_url = (base_url or (DEFAULT_CODEX_BASE_URL if auth_mode == "cli" else self.base_url)).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_models(self) -> list[ModelInfo]:
        """Return a stable starter model list without requiring a network call."""
        return list(DEFAULT_CODEX_MODELS)

    def complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        if self.auth_mode == "cli":
            return self._complete_responses(messages, model, options)
        payload: dict[str, object] = {"model": model, "messages": list(messages)}
        if options:
            payload.update(options)
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenAI Codex request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI Codex request failed: {exc}") from exc

        try:
            data = response.json()
            message = data["choices"][0]["message"]
            role = message["role"]
            content = message.get("content")
            tool_calls = message.get("tool_calls")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("OpenAI Codex returned an unexpected response shape.") from exc
        if role != "assistant" or not (
            isinstance(content, str) or isinstance(tool_calls, list)
        ):
            raise ProviderError("OpenAI Codex returned a malformed assistant message.")
        return Completion(message=dict(message), raw=data)

    def stream_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        if self.auth_mode == "cli":
            yield from self._stream_responses_events(messages, model, options)
            return
        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "stream": True,
        }
        if options:
            payload.update(options)
        try:
            with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = _parse_stream_line(line)
                    if event is not None:
                        yield event
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenAI Codex request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI Codex request failed: {exc}") from exc

    def stream_complete(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[str]:
        for event in self.stream_events(messages, model, options):
            if event.content is not None:
                yield event.content

    def _complete_responses(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        content = "".join(
            event.content or ""
            for event in self._stream_responses_events(messages, model, options)
        )
        return Completion(message={"role": "assistant", "content": content}, raw=None)

    def _stream_responses_events(
        self,
        messages: Sequence[Message],
        model: str,
        options: Mapping[str, object] | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        payload = _responses_payload(messages, model, options)
        try:
            with self._client.stream(
                "POST",
                f"{self.base_url}/responses",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = _parse_responses_stream_line(line)
                    if event is not None:
                        yield event
        except httpx.HTTPStatusError as exc:
            exc.response.read()
            detail = _response_error_detail(exc.response)
            raise ProviderError(f"OpenAI Codex request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI Codex request failed: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def _responses_payload(
    messages: Sequence[Message],
    model: str,
    options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    instructions: list[str] = []
    input_items: list[dict[str, object]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                instructions.append(content.strip())
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(content or ""),
                }
            )
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        text_type = "input_text" if role == "user" else "output_text"
        input_items.append(
            {
                "role": role,
                "content": [{"type": text_type, "text": str(content or "")}],
            }
        )
        tool_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )

    payload: dict[str, object] = {
        "model": model,
        "input": input_items,
        "stream": True,
        "store": False,
    }
    if instructions:
        payload["instructions"] = "\n\n".join(instructions)
    if options:
        tools = options.get("tools")
        response_tools = _responses_tools(tools if isinstance(tools, list) else None)
        if response_tools:
            payload["tools"] = response_tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
    return payload


def _responses_tools(tools: list[object] | None) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description", ""),
                "strict": False,
                "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _parse_responses_stream_line(line: str) -> ProviderStreamEvent | None:
    if not line.startswith("data:"):
        return None
    data_text = line.removeprefix("data:").strip()
    if not data_text or data_text == "[DONE]":
        return None
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError as exc:
        raise ProviderError("OpenAI Codex returned malformed Responses stream JSON.") from exc
    event_type = data.get("type")
    if event_type == "response.output_text.delta":
        delta = data.get("delta")
        if not isinstance(delta, str):
            raise ProviderError("OpenAI Codex returned malformed text delta.")
        return ProviderStreamEvent(content=delta)
    if event_type == "response.output_item.done":
        item = data.get("item")
        if isinstance(item, dict) and item.get("type") == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            arguments = str(item.get("arguments") or "{}")
            return ProviderStreamEvent(
                tool_calls=(
                    {
                        "index": int(data.get("output_index") or 0),
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    },
                )
            )
    if event_type == "response.failed":
        response = data.get("response")
        error = response.get("error") if isinstance(response, dict) else None
        raise ProviderError(f"OpenAI Codex response failed: {error or data_text[:300]}")
    return None


def codex_auth_candidate_paths() -> list[Path]:
    """Return Codex auth locations Akvan should try, in priority order."""
    candidates: list[Path] = []
    codex_home = os.getenv("CODEX_HOME", "").strip()
    if codex_home:
        candidates.append(Path(codex_home).expanduser() / "auth.json")
    candidates.extend(
        [
            Path.home() / ".codex" / "auth.json",
            Path.home() / "codex" / "auth.json",
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            deduped.append(candidate)
            seen.add(key)
    return deduped


def default_codex_auth_path() -> Path:
    for candidate in codex_auth_candidate_paths():
        if candidate.is_file():
            return candidate
    return codex_auth_candidate_paths()[0]


def load_codex_cli_token(path: Path | None = None) -> str:
    """Load a bearer token from the Codex CLI's local auth file.

    Tries ``$CODEX_HOME/auth.json`` first when CODEX_HOME is set, then the
    standard Codex CLI ``~/.codex/auth.json``, then ``~/codex/auth.json`` as a
    compatibility fallback. Requires the Codex CLI ``tokens`` object with both
    access and refresh tokens, and rejects expired access tokens.
    """
    auth_paths = [path] if path is not None else codex_auth_candidate_paths()
    missing_paths: list[Path] = []
    last_error: ProviderError | None = None
    for auth_path in auth_paths:
        try:
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            missing_paths.append(auth_path)
            continue
        except (OSError, json.JSONDecodeError) as exc:
            last_error = ProviderError(f"Could not read Codex CLI session at {auth_path}: {exc}")
            continue
        try:
            return _token_from_codex_auth_payload(payload, auth_path)
        except ProviderError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    checked = ", ".join(str(item) for item in missing_paths or auth_paths)
    raise ProviderError(
        f"Codex CLI session not found. Checked: {checked}. Run `codex login` or use API-key mode."
    )


def _token_from_codex_auth_payload(payload: object, auth_path: Path) -> str:
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict):
        raise ProviderError(
            f"Codex CLI session at {auth_path} is missing the `tokens` object. "
            "Run `codex login` again or use API-key mode."
        )
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ProviderError(
            f"Codex CLI session at {auth_path} is missing access_token. "
            "Run `codex login` again or use API-key mode."
        )
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ProviderError(
            f"Codex CLI session at {auth_path} is missing refresh_token. "
            "Run `codex login` again or use API-key mode."
        )
    access_token = access_token.strip()
    if _codex_access_token_is_expired(access_token):
        raise ProviderError(
            f"Codex CLI session at {auth_path} is expired. "
            "Run `codex login` again or use API-key mode."
        )
    return access_token


def _codex_access_token_is_expired(access_token: str) -> bool:
    parts = access_token.split(".")
    if len(parts) < 2:
        return False
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return False
    exp = data.get("exp") if isinstance(data, dict) else None
    return isinstance(exp, (int, float)) and exp <= __import__("time").time()


def _parse_stream_line(line: str) -> ProviderStreamEvent | None:
    if not line.startswith("data:"):
        return None
    data_text = line.removeprefix("data:").strip()
    if not data_text or data_text == "[DONE]":
        return None
    try:
        data = json.loads(data_text)
        delta = data["choices"][0].get("delta", {})
        content = delta.get("content")
        tool_calls = delta.get("tool_calls")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError("OpenAI Codex returned an unexpected streaming response shape.") from exc
    if content is not None and not isinstance(content, str):
        raise ProviderError("OpenAI Codex returned malformed streaming content.")
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise ProviderError("OpenAI Codex returned malformed streaming tool calls.")
    if content is None and not tool_calls:
        return None
    if tool_calls and not all(isinstance(call, dict) for call in tool_calls):
        raise ProviderError("OpenAI Codex returned malformed streaming tool calls.")
    return ProviderStreamEvent(content=content, tool_calls=tuple(tool_calls or ()))


def _response_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"HTTP {response.status_code}: {error['message']}"
        if isinstance(data.get("message"), str):
            return f"HTTP {response.status_code}: {data['message']}"
    return f"HTTP {response.status_code}: {data}"
