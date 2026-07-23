"""Deterministic history pruning and context compaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent.context.budget import (
    ContextBudget,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from agent.context.config import ContextConfig
from agent.messages import Message, tool_message_name

SUMMARY_MARKER = "[CONTEXT COMPACTION — REFERENCE ONLY]"
_PRUNED = "[Historical tool output summarized]"


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        from agent.messages import extract_message_text

        return extract_message_text(content)
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content or "")


def _prune_images_in_message(message: Message) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    from agent.vision.attach import prune_image_parts

    pruned = prune_image_parts(content)
    if pruned is content:
        return False
    message["content"] = pruned
    return True


def _tool_summary(message: Message, content: str) -> str:
    name = tool_message_name(message) or "tool"
    persisted = "Full output saved to:" in content
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    suffix = " (full output persisted)" if persisted else ""
    return f"{_PRUNED} [{name}] {first[:240]} — {len(content):,} chars{suffix}"


@dataclass(frozen=True)
class CompactionResult:
    messages: list[Message]
    before_tokens: int
    after_tokens: int
    pruned_results: int
    summarized_messages: int

    @property
    def changed(self) -> bool:
        return self.after_tokens < self.before_tokens


class ContextCompressor:
    def __init__(self, config: ContextConfig, budget: ContextBudget) -> None:
        self.config = config
        self.budget = budget

    def prune_old_tool_results(
        self,
        messages: list[Message],
        *,
        protected_start: int,
    ) -> tuple[list[Message], int]:
        result = [dict(message) for message in messages]
        hashes: dict[str, int] = {}
        pruned = 0
        for index in range(len(result) - 1, -1, -1):
            message = result[index]
            role = message.get("role")
            if role in {"tool", "user"} and index < protected_start:
                if _prune_images_in_message(message):
                    pruned += 1
            if role != "tool":
                continue
            content = _text(message.get("content"))
            if len(content) < 200:
                continue
            digest = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
            if digest in hashes:
                message["content"] = (
                    f"[Duplicate historical tool output; same as newer "
                    f"message {hashes[digest]}]"
                )
                pruned += 1
                continue
            hashes[digest] = index
            if index < protected_start and not content.startswith(_PRUNED):
                message["content"] = _tool_summary(message, content)
                pruned += 1

        for index in range(min(protected_start, len(result))):
            message = result[index]
            calls = message.get("tool_calls")
            if message.get("role") != "assistant" or not isinstance(calls, list):
                continue
            rewritten = []
            changed = False
            for call in calls:
                if not isinstance(call, dict):
                    rewritten.append(call)
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    rewritten.append(call)
                    continue
                args = function.get("arguments")
                if isinstance(args, str) and len(args) > 800:
                    try:
                        parsed = json.loads(args)
                    except json.JSONDecodeError:
                        parsed = {"truncated_arguments": args[:500]}
                    if isinstance(parsed, dict):
                        for key, value in list(parsed.items()):
                            if isinstance(value, str) and len(value) > 500:
                                parsed[key] = value[:400] + "...[truncated]"
                    function = {
                        **function,
                        "arguments": json.dumps(parsed, ensure_ascii=False),
                    }
                    call = {**call, "function": function}
                    changed = True
                rewritten.append(call)
            if changed:
                message["tool_calls"] = rewritten
        return result, pruned

    def _protected_tail_start(self, messages: list[Message]) -> int:
        token_budget = max(
            2_000,
            int(self.budget.compression_threshold_tokens * self.config.protect_recent_ratio),
        )
        used = 0
        start = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            cost = estimate_message_tokens(messages[index])
            if used + cost > token_budget and start < len(messages):
                break
            used += cost
            start = index
        # Do not pull a huge preceding tool turn back into the protected tail.
        # Advance to the next user boundary; the latest request always survives.
        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            max(0, len(messages) - 1),
        )
        while start < len(messages) and messages[start].get("role") != "user":
            start += 1
        if start >= len(messages):
            start = latest_user
        return start

    def _summarize(self, messages: list[Message], *, focus: str | None = None) -> str:
        lines = [
            SUMMARY_MARKER,
            "Earlier messages were compacted as historical background. "
            "The latest user message after this summary remains the active request.",
            "",
        ]
        if focus:
            lines.append(f"Compaction focus: {' '.join(focus.split())[:500]}")
        max_chars = self.config.summary_max_chars
        for message in messages:
            if message.get("_compressed_summary"):
                prior = _text(message.get("content"))
                prior = prior.replace(SUMMARY_MARKER, "", 1).strip()
                for line in prior.splitlines():
                    if not line.strip() or line.startswith("Earlier messages were compacted"):
                        continue
                    if sum(len(part) + 1 for part in lines) + len(line) > max_chars:
                        lines.append("- [additional historical details omitted]")
                        return "\n".join(lines)
                    lines.append(line)
                continue
            role = str(message.get("role") or "unknown")
            content = _text(message.get("content")).strip()
            if not content and message.get("tool_calls"):
                names = []
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict):
                        function = call.get("function")
                        if isinstance(function, dict):
                            names.append(str(function.get("name") or "tool"))
                content = "called tools: " + ", ".join(names)
            if not content:
                continue
            if role == "tool":
                line = _tool_summary(message, content)
            else:
                compact = " ".join(content.split())
                if len(compact) > 900:
                    compact = compact[:650] + " ... " + compact[-200:]
                line = f"- {role}: {compact}"
            if sum(len(part) + 1 for part in lines) + len(line) > max_chars:
                lines.append("- [additional historical details omitted]")
                break
            lines.append(line)
        return "\n".join(lines)

    def compact(
        self,
        messages: list[Message],
        *,
        force: bool = False,
        focus: str | None = None,
    ) -> CompactionResult:
        before = estimate_messages_tokens(messages)
        if len(messages) <= 4 and not force:
            return CompactionResult(list(messages), before, before, 0, 0)

        tail_start = self._protected_tail_start(messages)
        minimum_head = min(
            len(messages),
            max(1, self.config.protect_first_messages),
        )
        if tail_start <= minimum_head:
            pruned, count = self.prune_old_tool_results(
                messages, protected_start=tail_start
            )
            after = estimate_messages_tokens(pruned)
            return CompactionResult(pruned, before, after, count, 0)

        pruned, count = self.prune_old_tool_results(
            messages, protected_start=tail_start
        )
        middle = pruned[minimum_head:tail_start]
        summary: Message = {
            # Session persistence intentionally excludes the live system prompt.
            # Store summaries as assistant history so they survive resume/reload.
            "role": "assistant",
            "content": self._summarize(middle, focus=focus),
            "_compressed_summary": True,
        }
        compacted = pruned[:minimum_head] + [summary] + pruned[tail_start:]
        after = estimate_messages_tokens(compacted)
        return CompactionResult(
            compacted,
            before,
            after,
            count,
            len(middle),
        )
