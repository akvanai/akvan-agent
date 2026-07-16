"""Post-turn background memory and skill review."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Literal

from agent.agent import AgentLoop
from agent.memory.config import MemoryConfig
from agent.memory.store import MemoryStore
from agent.messages import Message, parse_tool_result_content, tool_message_name
from agent.providers.base import Provider
from agent.skills.provenance import (
    BACKGROUND_REVIEW,
    reset_current_write_origin,
    set_current_write_origin,
)
from agent.tools.approval import ApprovalManager
from agent.tools.memory_tools import build_memory_tools
from agent.tools.skill_manage_tools import build_skill_manage_tool

review_log = logging.getLogger("akvan.review")

ReviewNotifications = Literal["off", "on", "verbose"]

MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — preferences, role, or "
    "personal details worth remembering? Save to target='user'.\n"
    "2. Has the user expressed expectations about how you should behave, or have "
    "you learned stable environment/project facts? Save to target='memory'.\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be ACTIVE — "
    "most sessions produce at least one skill update when there was a correction "
    "or reusable technique. Signals: user corrected style/workflow, non-trivial "
    "fix or debugging path emerged, or a loaded skill was wrong or outdated.\n\n"
    "Preference order:\n"
    "  1. PATCH a skill that was loaded or consulted this session.\n"
    "  2. PATCH an existing umbrella skill that covers the class of work.\n"
    "  3. ADD a support file under an existing skill (references/, scripts/, templates/).\n"
    "  4. CREATE a new class-level skill when nothing exists.\n\n"
    "Protected: bundled skills (listed in the system prompt as bundled/user sync) "
    "must NOT be edited or deleted by this review.\n\n"
    "Do NOT capture transient setup errors or negative claims about tools.\n"
    "If nothing stands out, say 'Nothing to save.' and stop."
)

COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and update two things:\n\n"
    "**Memory**: save durable user facts and preferences with the memory tool.\n\n"
    "**Skills**: patch or create procedural skills when corrections or reusable "
    "techniques emerged. Follow the skill preference order from the skill review "
    "guidance: patch loaded skills first, then umbrellas, then support files, "
    "then create class-level skills. Do not edit bundled skills.\n\n"
    "Act on whichever dimension has real signal. If nothing stands out on either, "
    "say 'Nothing to save.' and stop."
)


def _review_prompt(*, review_memory: bool, review_skills: bool) -> str:
    if review_memory and review_skills:
        return COMBINED_REVIEW_PROMPT
    if review_memory:
        return MEMORY_REVIEW_PROMPT
    return SKILL_REVIEW_PROMPT


def summarize_review_actions(
    review_messages: list[Message],
    prior_count: int,
) -> str | None:
    actions: list[str] = []
    for message in review_messages[prior_count:]:
        if message.get("role") != "tool":
            continue
        tool_name = tool_message_name(message)
        if tool_name not in {"memory", "skill_manage"}:
            continue
        payload = parse_tool_result_content(message.get("content"))
        if payload is None or payload.get("success") is not True:
            continue
        if tool_name == "memory":
            target = payload.get("target", "memory")
            note = payload.get("message") or "updated"
            actions.append(f"Memory ({target}): {note}")
        else:
            note = payload.get("message") or "updated"
            actions.append(f"Skill: {note}")
    if not actions:
        return None
    return " · ".join(actions)


def spawn_background_review(
    *,
    provider: Provider,
    model: str,
    memory_store: MemoryStore | None,
    memory_config: MemoryConfig,
    messages_snapshot: list[Message],
    review_memory: bool = False,
    review_skills: bool = False,
    on_complete: Callable[[str | None], None] | None = None,
) -> None:
    if not review_memory and not review_skills:
        return
    if review_memory and not (
        memory_store is not None
        and (memory_config.memory_enabled or memory_config.user_profile_enabled)
    ):
        review_memory = False
    if not review_memory and not review_skills:
        return

    prompt = _review_prompt(review_memory=review_memory, review_skills=review_skills)

    def _run() -> None:
        notification: str | None = None
        token = set_current_write_origin(BACKGROUND_REVIEW)
        review_log.info("background review started memory=%s skills=%s", review_memory, review_skills)
        try:
            tools: list[Any] = []
            if review_memory and memory_store is not None:
                tools.extend(build_memory_tools(memory_store))
            if review_skills:
                tools.append(build_skill_manage_tool())
            if not tools:
                return
            loop = AgentLoop(
                provider=provider,
                model=model,
                max_iterations=8,
                tools=tuple(tools),
                approval_manager=ApprovalManager(mode="off"),
            )
            review_messages = list(messages_snapshot)
            prior_count = len(review_messages)
            suffix = (
                "\n\nYou can only call memory and skill_manage tools. "
                "Other tools will be denied — do not attempt them."
            )
            review_messages.append({"role": "user", "content": prompt + suffix})
            loop.run_turn(review_messages, "")
            notification = summarize_review_actions(review_messages, prior_count)
            if notification:
                review_log.info("background review completed: %s", notification)
            else:
                review_log.info("background review completed: nothing to save")
        except Exception as exc:
            review_log.warning("background review failed: %s", exc)
        finally:
            reset_current_write_origin(token)
        if on_complete is not None:
            try:
                on_complete(notification)
            except Exception:
                review_log.warning("background review on_complete failed", exc_info=True)

    thread = threading.Thread(target=_run, name="akvan-bg-review", daemon=True)
    thread.start()


def spawn_memory_review(
    *,
    provider: Provider,
    model: str,
    memory_store: MemoryStore,
    memory_config: MemoryConfig,
    messages_snapshot: list[Message],
    on_complete: Callable[[str | None], None] | None = None,
) -> None:
    """Backward-compatible wrapper for memory-only review."""
    spawn_background_review(
        provider=provider,
        model=model,
        memory_store=memory_store,
        memory_config=memory_config,
        messages_snapshot=messages_snapshot,
        review_memory=True,
        review_skills=False,
        on_complete=on_complete,
    )
