"""Bound large tool results while preserving complete output on disk."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agent.context.budget import ContextBudget
from agent.context.config import ContextConfig
from agent.messages import Message
from agent.tools.base import ToolResult

logger = logging.getLogger(__name__)
PERSISTED_TAG = "<persisted-output>"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_SECRET_VALUE = re.compile(
    r"(?im)\b(api[_-]?key|authorization|access[_-]?token|token|secret|password)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def _safe(value: str) -> str:
    cleaned = _SAFE_ID.sub("-", value).strip(".-")
    return cleaned[:96] or "result"


def _preview(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    head = content[:limit]
    newline = head.rfind("\n")
    if newline >= limit // 2:
        head = head[:newline]
    return head


def _redact_preview(content: str) -> str:
    content = _SECRET_VALUE.sub(r"\1\2[REDACTED]", content)
    content = _BEARER.sub("Bearer [REDACTED]", content)
    return _OPENAI_KEY.sub("[REDACTED]", content)


@dataclass
class ToolResultStore:
    root: Path
    budget: ContextBudget
    config: ContextConfig
    session_id: str = "session"

    def __post_init__(self) -> None:
        self.root = self.root.expanduser()

    def _ensure_dir(self) -> Path:
        target = self.root / _safe(self.session_id)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(target, 0o700)
        except OSError:
            pass
        return target

    def cleanup(self) -> None:
        cutoff = time.time() - self.config.result_retention_days * 86400
        if not self.root.is_dir():
            return
        for path in self.root.rglob("*.txt"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def bound_result(
        self,
        result: ToolResult,
        *,
        tool_name: str,
        call_id: str,
        force: bool = False,
    ) -> ToolResult:
        content = result.content
        if (
            not self.config.persist_oversized_results
            or (not force and len(content) <= self.budget.max_result_chars)
        ):
            return result

        preview = _redact_preview(_preview(content, self.budget.preview_chars))
        path = self._ensure_dir() / f"{_safe(call_id)}-{_safe(tool_name)}.txt"
        try:
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o600)
            replacement = (
                f"{PERSISTED_TAG}\n"
                f"Tool result {tool_name!r} was too large "
                f"({len(content):,} characters).\n"
                f"Full output saved to: {path}\n"
                "Use the read_file tool with offset and limit to inspect only "
                "the relevant sections.\n\n"
                f"Preview (first {len(preview):,} characters):\n"
                f"{preview}\n"
                "</persisted-output>"
            )
            logger.info(
                "Persisted oversized tool result tool=%s chars=%d path=%s",
                tool_name,
                len(content),
                path,
            )
        except OSError as exc:
            replacement = (
                f"{preview}\n\n"
                f"[Tool result truncated from {len(content):,} characters; "
                f"full-output persistence failed: {exc}]"
            )
            logger.warning(
                "Could not persist oversized tool result tool=%s: %s",
                tool_name,
                exc,
            )
        return ToolResult(replacement, result.kind, images=result.images)

    def enforce_turn_budget(self, messages: list[Message], indices: list[int]) -> None:
        candidates: list[tuple[int, int]] = []
        total = 0
        for index in indices:
            if not 0 <= index < len(messages):
                continue
            content = messages[index].get("content")
            if isinstance(content, list):
                from agent.messages import extract_message_text

                text = extract_message_text(content)
                total += len(text)
                # Multimodal tool results are not force-persisted as plain text;
                # image parts stay until compaction prunes them.
                continue
            if not isinstance(content, str):
                continue
            total += len(content)
            if PERSISTED_TAG not in content:
                candidates.append((index, len(content)))
        if total <= self.budget.max_turn_chars:
            return

        for index, size in sorted(candidates, key=lambda item: item[1], reverse=True):
            if total <= self.budget.max_turn_chars:
                break
            message = messages[index]
            content = str(message.get("content") or "")
            name = str(message.get("name") or message.get("tool_name") or "tool")
            call_id = str(message.get("tool_call_id") or f"turn-{index}")
            bounded = self.bound_result(
                ToolResult(content),
                tool_name=name,
                call_id=call_id,
                force=True,
            ).content
            if bounded != content:
                message["content"] = bounded
                total += len(bounded) - size
