"""
Coordinates one user turn between conversation history and a provider.
Streams response chunks while enforcing the configured iteration limit.
Converts unexpected provider failures into clear agent-loop errors.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from agent.events import AgentEvent, AgentState
from agent.messages import Message, TurnContext
from agent.providers.base import Provider, ProviderError
from agent.providers.deepseek import needs_reasoning_content_pad
from agent.tools.approval import ApprovalManager, ApprovalRequirement
from agent.tools.base import Tool, ToolResult

DEFAULT_MAX_ITERATIONS = 30


class AgentLoopError(RuntimeError):
    """Raised when the agent loop cannot produce a final response."""


@dataclass
class AgentLoop:
    provider: Provider
    model: str
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    options: Mapping[str, object] | None = None
    tools: tuple[Tool, ...] = ()
    approval_manager: ApprovalManager = field(default_factory=ApprovalManager)
    session_cost_usd: float | None = field(init=False, default=None)
    _tool_schemas: tuple[dict[str, object], ...] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.set_tools(self.tools)

    def set_tools(self, tools: tuple[Tool, ...]) -> None:
        self.tools = tools
        self._tool_schemas = tuple(tool.provider_schema() for tool in tools)

    def run_turn(
        self,
        messages: list[Message],
        user_input: str,
        *,
        turn_context: TurnContext | None = None,
    ) -> str:
        chunks = list(
            self.stream_turn(messages, user_input, turn_context=turn_context)
        )
        return "".join(chunks)

    def stream_turn(
        self,
        messages: list[Message],
        user_input: str,
        *,
        turn_context: TurnContext | None = None,
    ) -> Iterator[str]:
        """Yield public answer text while retaining the original API."""

        for event in self.stream_events(
            messages, user_input, turn_context=turn_context
        ):
            if event.content is not None:
                yield event.content

    def stream_events(
        self,
        messages: list[Message],
        user_input: str,
        *,
        turn_context: TurnContext | None = None,
    ) -> Iterator[AgentEvent]:
        """Yield safe activity transitions and public answer chunks for one turn."""

        if self.max_iterations < 1:
            raise AgentLoopError("max_iterations must be at least 1.")

        messages.append({"role": "user", "content": user_input})
        user_index = len(messages) - 1
        yield AgentEvent(AgentState.THINKING)

        for _ in range(self.max_iterations):
            chunks: list[str] = []
            reasoning_chunks: list[str] = []
            tool_call_parts: dict[int, dict[str, object]] = {}
            responding = False
            options = self._options_with_tools() if self.tools else self.options
            try:
                for provider_event in self.provider.stream_events(
                    messages=self._request_messages(messages, user_index, turn_context),
                    model=self.model,
                    options=options,
                ):
                    request_cost = provider_event.cost_usd
                    if request_cost is not None:
                        if (
                            isinstance(request_cost, bool)
                            or not isinstance(request_cost, (int, float))
                            or not math.isfinite(float(request_cost))
                            or request_cost < 0
                        ):
                            raise AgentLoopError(
                                "Provider returned an invalid request cost."
                            )
                        self.session_cost_usd = (
                            (self.session_cost_usd or 0.0) + float(request_cost)
                        )
                    self._merge_tool_call_deltas(
                        tool_call_parts, provider_event.tool_calls
                    )
                    reasoning = provider_event.reasoning_content
                    if reasoning is not None:
                        if not isinstance(reasoning, str):
                            raise AgentLoopError(
                                "Provider returned malformed reasoning content."
                            )
                        reasoning_chunks.append(reasoning)
                    content = provider_event.content
                    if content is None:
                        continue
                    if not isinstance(content, str):
                        raise AgentLoopError(
                            "Provider returned a malformed streaming chunk."
                        )
                    if not responding:
                        responding = True
                        yield AgentEvent(AgentState.RESPONDING)
                    chunks.append(content)
                    yield AgentEvent(AgentState.RESPONDING, content=content)
            except ProviderError:
                yield AgentEvent(AgentState.FAILED)
                raise
            except AgentLoopError:
                yield AgentEvent(AgentState.FAILED)
                raise
            except Exception as exc:
                yield AgentEvent(AgentState.FAILED)
                raise AgentLoopError(
                    f"Provider failed unexpectedly: {exc}"
                ) from exc

            content = "".join(chunks)
            if tool_call_parts:
                tool_calls = [tool_call_parts[index] for index in sorted(tool_call_parts)]
                assistant_message: Message = {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls,
                }
                reasoning_content = "".join(reasoning_chunks)
                if reasoning_content:
                    assistant_message["reasoning_content"] = reasoning_content
                elif self._needs_reasoning_content_pad():
                    assistant_message["reasoning_content"] = " "
                messages.append(assistant_message)
                try:
                    yield from self._run_tool_calls(messages, tool_calls)
                except AgentLoopError:
                    yield AgentEvent(AgentState.FAILED)
                    raise
                except Exception as exc:
                    yield AgentEvent(AgentState.FAILED)
                    raise AgentLoopError(f"Tool execution failed: {exc}") from exc
                yield AgentEvent(AgentState.THINKING)
                continue

            if not responding:
                yield AgentEvent(AgentState.RESPONDING)
            messages.append({"role": "assistant", "content": content})
            yield AgentEvent(AgentState.COMPLETED)
            return

        yield AgentEvent(AgentState.FAILED)
        raise AgentLoopError(
            f"Agent reached the max iteration limit of {self.max_iterations}."
        )

    def _request_messages(
        self,
        messages: list[Message],
        user_index: int,
        turn_context: TurnContext | None,
    ) -> list[Message]:
        if turn_context is None or turn_context.provider_user_content is None:
            return messages
        request_messages = list(messages)
        user_message = dict(request_messages[user_index])
        user_message["content"] = turn_context.provider_user_content
        request_messages[user_index] = user_message
        return request_messages

    def _options_with_tools(self) -> dict[str, object]:
        options = dict(self.options or {})
        options["tools"] = list(self._tool_schemas)
        return options

    def _needs_reasoning_content_pad(self) -> bool:
        base_url = getattr(self.provider, "base_url", "")
        if not isinstance(base_url, str):
            base_url = ""
        return needs_reasoning_content_pad(self.provider.name, self.model, base_url)

    @staticmethod
    def _merge_tool_call_deltas(
        parts: dict[int, dict[str, object]],
        deltas: tuple[dict[str, object], ...],
    ) -> None:
        for position, delta in enumerate(deltas):
            raw_index = delta.get("index", position)
            if not isinstance(raw_index, int) or raw_index < 0:
                raise AgentLoopError("Provider returned an invalid tool-call index.")
            part = parts.setdefault(
                raw_index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            call_id = delta.get("id")
            if call_id is not None:
                if not isinstance(call_id, str):
                    raise AgentLoopError("Provider returned an invalid tool-call id.")
                part["id"] = call_id
            call_type = delta.get("type")
            if call_type is not None:
                if not isinstance(call_type, str):
                    raise AgentLoopError("Provider returned an invalid tool-call type.")
                part["type"] = call_type
            function_delta = delta.get("function")
            if function_delta is None:
                continue
            if not isinstance(function_delta, dict):
                raise AgentLoopError("Provider returned an invalid tool function.")
            function = part["function"]
            if not isinstance(function, dict):
                raise AgentLoopError("Provider returned an invalid tool function.")
            name = function_delta.get("name")
            if name is not None:
                if not isinstance(name, str):
                    raise AgentLoopError("Provider returned an invalid tool name.")
                function["name"] = str(function.get("name", "")) + name
            arguments = function_delta.get("arguments")
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise AgentLoopError("Provider returned invalid tool arguments.")
                function["arguments"] = str(function.get("arguments", "")) + arguments

    def _run_tool_calls(
        self, messages: list[Message], tool_calls: object
    ) -> Iterator[AgentEvent]:
        if not isinstance(tool_calls, list):
            raise AgentLoopError("Provider returned malformed tool calls.")

        tools_by_name = {tool.name: tool for tool in self.tools}
        for call in tool_calls:
            if not isinstance(call, dict):
                raise AgentLoopError("Provider returned a malformed tool call.")
            call_id = call.get("id")
            function = call.get("function")
            if not isinstance(call_id, str) or not isinstance(function, dict):
                raise AgentLoopError("Provider returned a malformed tool call.")

            name = function.get("name")
            raw_arguments = function.get("arguments", "{}")
            if not isinstance(name, str) or not isinstance(raw_arguments, str):
                raise AgentLoopError("Provider returned malformed tool arguments.")
            tool = tools_by_name.get(name)
            if tool is None:
                raise AgentLoopError(f"Provider requested unknown tool {name!r}.")

            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise AgentLoopError(
                    f"Provider returned invalid arguments for tool {name!r}."
                ) from exc
            if not isinstance(arguments, dict):
                raise AgentLoopError(f"Arguments for tool {name!r} must be an object.")

            requirement: ApprovalRequirement | None = None
            if tool.approval is not None:
                raw_requirement = tool.approval(arguments)
                if raw_requirement is not None and not isinstance(
                    raw_requirement, ApprovalRequirement
                ):
                    raise AgentLoopError(
                        f"Approval policy for tool {name!r} returned invalid data."
                    )
                requirement = raw_requirement
            approval = self.approval_manager.prepare(name, requirement)
            if approval.request is not None and requirement is not None:
                request = approval.request
                yield AgentEvent(
                    AgentState.AWAITING_APPROVAL,
                    tool_name=name,
                    tool_arguments=arguments,
                    request_id=request.request_id,
                    summary=request.summary,
                    reason=request.reason,
                    choices=tuple(choice.value for choice in request.choices),
                )
                approval = self.approval_manager.resolve(request, requirement)
            if not approval.allowed:
                yield AgentEvent(
                    AgentState.RUNNING_TOOL,
                    tool_name=name,
                    tool_arguments=arguments,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": ToolResult(
                            f"Operation was not executed: {approval.message}"
                        ).render(source=name),
                    }
                )
                continue

            yield AgentEvent(
                AgentState.RUNNING_TOOL,
                tool_name=name,
                tool_arguments=arguments,
            )
            try:
                tool_result = tool.invoke(arguments)
            except Exception as exc:
                tool_result = ToolResult(f"Error: {exc}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": tool_result.render(source=name),
                }
            )
