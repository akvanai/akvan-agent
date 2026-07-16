"""Dedicated periodic review of persisted conversations into global knowledge."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent.agent import AgentLoop
from agent.knowledge.paths import knowledge_review_state_file, knowledge_state_dir
from agent.knowledge.store import KnowledgeStore
from agent.messages import Message, parse_tool_result_content, tool_message_name
from agent.providers.base import Provider
from agent.storage.permissions import ensure_private_dir, ensure_private_file
from agent.storage.store import SessionStore
from agent.tools.approval import ApprovalManager
from agent.tools.knowledge_tools import build_knowledge_tools

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


review_log = logging.getLogger("akvan.review")
_thread_lock = threading.Lock()

CURATOR_INSTRUCTION = """# Global Knowledge Curator

Review only the conversation messages provided to you. Maintain durable, detailed,
user-specific knowledge in the OKF bundle.

Classify candidates strictly:
- Temporary request: ignore.
- Small personal preference: leave for memory review.
- Reusable procedure: leave for skill review.
- Detailed durable fact about an important subject: knowledge.
- Secret, credential, or unsafe private value: never store.

For knowledge candidates:
1. Search before writing and read a related concept before updating it.
2. Prefer augmenting an existing concept over creating a duplicate.
3. Preserve all useful existing frontmatter, body sections, and facts.
4. Choose descriptive lowercase concept paths and dynamic concept types.
5. Add normal Markdown links to related concepts when useful.
6. Use high confidence only for clear user statements.
7. Evidence kind explicit_user requires a short verbatim quote from a user message.
8. Mark conflict=true for any contradiction or uncertain replacement.
9. Never propose deletion. When in doubt, skip.

Use only knowledge_search, knowledge_read, and knowledge_propose. Do not narrate
reasoning. If nothing qualifies, finish without a tool call.
"""


def read_review_state(state_root: Path | None = None) -> dict[str, object]:
    path = (state_root / "review.json") if state_root else knowledge_review_state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_message_id": 0}
    return data if isinstance(data, dict) else {"last_message_id": 0}


def persisted_review_batch(
    session_store: SessionStore | None,
    knowledge_store: KnowledgeStore,
) -> tuple[int, list[Message]] | None:
    config = knowledge_store.config
    if session_store is None or config.review_interval <= 0:
        return None
    state = read_review_state(knowledge_store.state_root)
    cursor = int(state.get("last_message_id", 0) or 0)
    return session_store.knowledge_review_batch(
        after_message_id=cursor,
        user_turn_limit=config.review_interval,
    )


def summarize_knowledge_actions(messages: list[Message], prior_count: int) -> str | None:
    applied = 0
    pending = 0
    for message in messages[prior_count:]:
        if message.get("role") != "tool" or tool_message_name(message) != "knowledge_propose":
            continue
        payload = parse_tool_result_content(message.get("content"))
        if payload is None or payload.get("success") is not True:
            continue
        if payload.get("status") == "applied":
            applied += 1
        elif payload.get("status") == "pending":
            pending += 1
    parts: list[str] = []
    if applied:
        parts.append(f"Knowledge: {applied} automatic update(s)")
    if pending:
        parts.append(f"Knowledge: {pending} proposal(s) need review")
    return " · ".join(parts) or None


def spawn_knowledge_review(
    *,
    provider: Provider,
    model: str,
    knowledge_store: KnowledgeStore,
    messages_snapshot: list[Message],
    high_water_message_id: int | None,
    on_complete: Callable[[str | None], None] | None = None,
) -> None:
    """Run one bounded curator pass without blocking the active conversation."""

    def _run() -> None:
        notification: str | None = None
        with _review_process_lock(knowledge_store.state_root) as acquired:
            if not acquired:
                return
            state = read_review_state(knowledge_store.state_root)
            if high_water_message_id is not None and int(
                state.get("last_message_id", 0) or 0
            ) >= high_water_message_id:
                return
            review_log.info("knowledge review started messages=%s", len(messages_snapshot))
            try:
                user_messages = [
                    str(message.get("content") or "")
                    for message in messages_snapshot
                    if message.get("role") == "user"
                ]
                tools = build_knowledge_tools(
                    knowledge_store,
                    user_messages=lambda: user_messages,
                    include_manage=False,
                )
                loop = AgentLoop(
                    provider=provider,
                    model=model,
                    max_iterations=8,
                    tools=tools,
                    approval_manager=ApprovalManager(mode="off"),
                )
                review_messages: list[Message] = [
                    {"role": "system", "content": CURATOR_INSTRUCTION}
                ]
                review_messages.extend(
                    {"role": message["role"], "content": message.get("content", "")}
                    for message in messages_snapshot
                    if message.get("role") in {"user", "assistant"}
                )
                prior_count = len(review_messages)
                loop.run_turn(
                    review_messages,
                    "Review this conversation batch now. Curate only strong reusable knowledge.",
                )
                notification = summarize_knowledge_actions(review_messages, prior_count)
                _write_review_state(high_water_message_id, knowledge_store.state_root)
                review_log.info(
                    "knowledge review completed: %s", notification or "nothing to save"
                )
            except Exception as exc:
                review_log.warning("knowledge review failed: %s", exc)
                return
        if on_complete is not None:
            try:
                on_complete(notification)
            except Exception:
                review_log.warning("knowledge review on_complete failed", exc_info=True)

    threading.Thread(target=_run, name="akvan-knowledge-review", daemon=True).start()


def _write_review_state(high_water_message_id: int | None, state_root: Path) -> None:
    state = read_review_state(state_root)
    if high_water_message_id is not None:
        state["last_message_id"] = high_water_message_id
    state["last_review_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    root = ensure_private_dir(state_root)
    path = root / "review.json"
    fd, temp_name = tempfile.mkstemp(prefix=".review.", dir=root)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(0o600)
        os.replace(temp, path)
        ensure_private_file(path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _review_process_lock(state_root: Path | None = None) -> Iterator[bool]:
    root = ensure_private_dir(state_root or knowledge_state_dir())
    path = root / "review.lock"
    with _thread_lock, path.open("a+", encoding="utf-8") as handle:
        ensure_private_file(path)
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
