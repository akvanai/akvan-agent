"""
Coordinates one user turn between conversation history and a provider.
Streams response chunks while enforcing the configured iteration limit.
Converts unexpected provider failures into clear agent-loop errors.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent.config import akvan_home
from agent.context import (
    CompactionResult,
    ContextBudget,
    ContextCompressor,
    ContextConfig,
    RequestBreakdown,
    ToolResultStore,
)
from agent.context.tool_search import build_disclosure
from agent.events import AgentEvent, AgentState
from agent.messages import Message, TurnContext
from agent.providers.base import Provider, ProviderError
from agent.tools.approval import ApprovalManager, ApprovalRequirement
from agent.tools.base import Tool, ToolResult

DEFAULT_MAX_ITERATIONS = 30
MAX_CONTEXT_RECOVERY_ATTEMPTS = 3
logger = logging.getLogger(__name__)


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
    context_config: ContextConfig = field(default_factory=ContextConfig)
    result_store_root: Path | None = None
    session_id: str = "session"
    compaction_callback: Callable[[list[Message]], None] | None = None
    session_cost_usd: float | None = field(init=False, default=None)
    last_context_usage: RequestBreakdown | None = field(init=False, default=None)
    last_compaction: CompactionResult | None = field(init=False, default=None)
    deferred_tool_count: int = field(init=False, default=0)
    _all_tools: tuple[Tool, ...] = field(init=False, repr=False, default=())
    _tool_schemas: tuple[dict[str, object], ...] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.context_budget = ContextBudget.for_model(
            self.model, self.context_config
        )
        self.context_compressor = ContextCompressor(
            self.context_config, self.context_budget
        )
        result_root = self.result_store_root or (
            akvan_home() / "tmp" / "tool-results"
        )
        self.result_store = ToolResultStore(
            result_root,
            self.context_budget,
            self.context_config,
            session_id=self.session_id,
        )
        self.result_store.cleanup()
        self.set_tools(self.tools)

    def set_tools(self, tools: tuple[Tool, ...]) -> None:
        self._all_tools = tools
        disclosure = build_disclosure(
            tools,
            config=self.context_config,
            budget=self.context_budget,
        )
        self.tools = disclosure.visible
        self.deferred_tool_count = len(disclosure.deferred)
        self._tool_schemas = tuple(
            tool.provider_schema() for tool in self.tools
        )

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
        context_recovery_attempts = 0

        for _ in range(self.max_iterations):
            chunks: list[str] = []
            reasoning_chunks: list[str] = []
            tool_call_parts: dict[int, dict[str, object]] = {}
            responding = False
            options = self._options_with_tools() if self.tools else self.options
            request_messages = self._request_messages(
                messages, user_index, turn_context
            )
            self.last_context_usage = self.context_budget.estimate(
                request_messages,
                self._tool_schemas,
            )
            logger.debug(
                "Context preflight messages=%d schemas=%d reserved_output=%d "
                "input=%d effective_input=%d threshold=%d",
                self.last_context_usage.messages,
                self.last_context_usage.tool_schemas,
                self.last_context_usage.reserved_output,
                self.last_context_usage.estimated_total,
                self.last_context_usage.effective_input,
                self.last_context_usage.threshold,
            )
            if (
                self.context_config.enabled
                and self.context_config.compression_enabled
                and self.last_context_usage.estimated_total
                >= self.context_budget.compression_threshold_tokens
            ):
                changed = self._compact_messages(messages)
                if changed:
                    user_index = self._latest_user_index(messages)
                    request_messages = self._request_messages(
                        messages, user_index, turn_context
                    )
                    self.last_context_usage = self.context_budget.estimate(
                        request_messages,
                        self._tool_schemas,
                    )
            if (
                self.context_config.enabled
                and self.last_context_usage.estimated_total
                >= self.last_context_usage.effective_input
            ):
                yield AgentEvent(AgentState.FAILED)
                raise ProviderError(
                    "Akvan stopped an oversized request before sending it: "
                    f"estimated input is {self.last_context_usage.estimated_total:,} "
                    f"tokens but only {self.last_context_usage.effective_input:,} "
                    "are available after reserving output. Run /compress, start "
                    "a new session, reduce the active skill/input, or choose a "
                    "larger-context model."
                )
            try:
                for provider_event in self.provider.stream_events(
                    messages=request_messages,
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
            except ProviderError as exc:
                if (
                    self.context_config.enabled
                    and self.context_config.compression_enabled
                    and self._is_context_overflow(exc)
                    and context_recovery_attempts
                    < MAX_CONTEXT_RECOVERY_ATTEMPTS
                ):
                    context_recovery_attempts += 1
                    if self._compact_messages(messages, force=True):
                        user_index = self._latest_user_index(messages)
                        logger.warning(
                            "Provider context overflow; compacted and retrying "
                            "attempt=%d/%d",
                            context_recovery_attempts,
                            MAX_CONTEXT_RECOVERY_ATTEMPTS,
                        )
                        yield AgentEvent(AgentState.THINKING)
                        continue
                yield AgentEvent(AgentState.FAILED)
                if self._is_context_overflow(exc):
                    raise ProviderError(
                        f"{exc} Akvan could not reduce context further after "
                        f"{context_recovery_attempts} recovery attempt(s). "
                        "Run /compress, start a new session, or use a "
                        "larger-context model."
                    ) from exc
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
                elif self.provider.needs_reasoning_content_pad(self.model):
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

    @staticmethod
    def _latest_user_index(messages: list[Message]) -> int:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index
        return max(0, len(messages) - 1)

    @staticmethod
    def _is_context_overflow(exc: ProviderError) -> bool:
        text = str(exc).lower()
        markers = (
            "context_length_exceeded",
            "context length",
            "context window",
            "maximum context",
            "prompt too long",
            "input is too long",
            "request too large",
            "payload too large",
            "http 413",
            "status 413",
        )
        return any(marker in text for marker in markers)

    def _compact_messages(
        self, messages: list[Message], *, force: bool = False
    ) -> bool:
        result = self.context_compressor.compact(messages, force=force)
        self.last_compaction = result
        if not result.changed:
            return False
        messages[:] = result.messages
        if self.compaction_callback is not None:
            self.compaction_callback(messages)
        logger.info(
            "Context compacted tokens=%d->%d pruned=%d summarized=%d",
            result.before_tokens,
            result.after_tokens,
            result.pruned_results,
            result.summarized_messages,
        )
        return True

    def compact_context(
        self,
        messages: list[Message],
        *,
        force: bool = True,
        focus: str | None = None,
    ) -> CompactionResult:
        result = self.context_compressor.compact(
            messages, force=force, focus=focus
        )
        self.last_compaction = result
        if result.changed:
            messages[:] = result.messages
            if self.compaction_callback is not None:
                self.compaction_callback(messages)
        if self.last_compaction is None:
            tokens = self.context_budget.estimate(
                messages, self._tool_schemas
            ).messages
            return CompactionResult(list(messages), tokens, tokens, 0, 0)
        return self.last_compaction

    def context_usage(self, messages: list[Message]) -> RequestBreakdown:
        usage = self.context_budget.estimate(messages, self._tool_schemas)
        self.last_context_usage = usage
        return usage

    def update_session_id(self, session_id: str) -> None:
        self.session_id = session_id
        self.result_store.session_id = session_id

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
        result_indices: list[int] = []
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
            tool_result = self.result_store.bound_result(
                tool_result,
                tool_name=name,
                call_id=call_id,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": tool_result.render(source=name),
                }
            )
            result_indices.append(len(messages) - 1)
        self.result_store.enforce_turn_budget(messages, result_indices)
