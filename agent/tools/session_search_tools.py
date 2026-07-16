"""Search past Akvan sessions via FTS5."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from agent.storage.store import SessionStore
from agent.tools.base import Tool


@dataclass
class SessionSearchContext:
    store: SessionStore | None
    current_session_id: Callable[[], str]


def _format_timestamp(ts: float | str | None) -> str:
    if ts is None:
        return "unknown"
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).strftime("%B %d, %Y at %I:%M %p")
        return str(ts)
    except (ValueError, OSError, OverflowError):
        return str(ts)


def session_search(
    *,
    query: str | None = None,
    session_id: str | None = None,
    around_message_id: int | None = None,
    window: int = 5,
    limit: int = 5,
    ctx: SessionSearchContext | None = None,
) -> str:
    if ctx is None or ctx.store is None:
        return json.dumps(
            {"success": False, "error": "Session database is not available."},
            ensure_ascii=False,
        )
    store = ctx.store
    current_id = ctx.current_session_id()

    if session_id and around_message_id is not None:
        rows = store.get_messages_around(
            session_id.strip(),
            int(around_message_id),
            window=window,
        )
        return json.dumps(
            {
                "success": True,
                "mode": "scroll",
                "session_id": session_id,
                "around_message_id": around_message_id,
                "messages": rows,
            },
            ensure_ascii=False,
        )

    if session_id and not query:
        meta, messages, truncated = store.get_session_messages_with_ids(session_id)
        if meta is None:
            return json.dumps(
                {"success": False, "error": f"session_id not found: {session_id}"},
                ensure_ascii=False,
            )
        response: dict[str, Any] = {
            "success": True,
            "mode": "read",
            "session_id": session_id,
            "session_meta": {
                "when": _format_timestamp(meta.get("started_at")),
                "source": meta.get("source"),
                "model": meta.get("model"),
                "title": meta.get("title"),
            },
            "message_count": meta.get("message_count", len(messages)),
            "truncated": truncated,
            "messages": messages,
        }
        if truncated:
            response["message"] = (
                "Session is large; showing head and tail. "
                "Pass around_message_id to scroll the middle."
            )
        return json.dumps(response, ensure_ascii=False)

    if query and query.strip():
        hits = store.search_messages(
            query.strip(),
            limit=max(limit, 1) * 10,
            exclude_session_id=current_id or None,
        )
        seen_sessions: set[str] = set()
        results: list[dict[str, Any]] = []
        for hit in hits:
            sid = str(hit["session_id"])
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            mid = int(hit["message_id"])
            context = store.get_messages_around(sid, mid, window=window)
            results.append({
                "session_id": sid,
                "title": hit.get("title"),
                "source": hit.get("source"),
                "started_at": hit.get("started_at"),
                "snippet": hit.get("snippet"),
                "anchor_message_id": mid,
                "messages": context,
            })
            if len(results) >= limit:
                break
        return json.dumps(
            {
                "success": True,
                "mode": "discover",
                "query": query,
                "results": results,
                "count": len(results),
            },
            ensure_ascii=False,
        )

    rows = store.list_sessions(
        limit=limit,
        exclude_session_id=current_id or None,
    )
    return json.dumps(
        {
            "success": True,
            "mode": "browse",
            "results": rows,
            "count": len(rows),
            "message": "Pass query= to search, or session_id+around_message_id to scroll.",
        },
        ensure_ascii=False,
    )


SESSION_SEARCH_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "FTS5 keyword search across past sessions (discover mode).",
        },
        "session_id": {
            "type": "string",
            "description": "Session id for scroll/read mode.",
        },
        "around_message_id": {
            "type": "integer",
            "description": "Anchor message id for scroll mode.",
        },
        "window": {
            "type": "integer",
            "description": "Messages before/after anchor (default 5).",
        },
        "limit": {
            "type": "integer",
            "description": "Max sessions in discover/browse (default 5).",
        },
    },
}


def build_session_search_tools(
    ctx: SessionSearchContext | None,
) -> tuple[Tool, ...]:
    if ctx is None or ctx.store is None:
        return ()

    def run(
        *,
        query: str | None = None,
        session_id: str | None = None,
        around_message_id: int | None = None,
        window: int = 5,
        limit: int = 5,
    ) -> str:
        return session_search(
            query=query,
            session_id=session_id,
            around_message_id=around_message_id,
            window=window,
            limit=limit,
            ctx=ctx,
        )

    return (
        Tool(
            name="session_search",
            description=(
                "Search past conversations stored in SQLite. Modes: pass query for "
                "FTS5 discovery; pass session_id + around_message_id to scroll; "
                "no args to browse recent sessions. No LLM calls — returns raw messages."
            ),
            parameters=SESSION_SEARCH_PARAMETERS,
            run=run,
        ),
    )
