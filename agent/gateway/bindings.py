"""Gateway chat-to-session binding helpers."""

from __future__ import annotations

import uuid

from typing import Callable

from agent.session import AgentSession
from agent.storage.store import SessionStore


def cache_key(platform: str, chat_id: str) -> str:
    return f"{platform}:{chat_id}"


def _persist_gateway_session(store: SessionStore, session: AgentSession) -> None:
    store.ensure_session_exists(
        session.session_id,
        source=session.session_source,
        model=session.model,
        provider=session.provider.name,
        cwd=str(session.prompt_builder.cwd),
    )


def get_or_create_session(
    *,
    platform: str,
    chat_id: str,
    store: SessionStore,
    session_cache: dict[str, AgentSession],
    factory: Callable[..., AgentSession],
) -> AgentSession:
    """Return a cached or newly created session for a gateway chat."""
    key = cache_key(platform, chat_id)
    cached = session_cache.get(key)
    if cached is not None:
        return cached

    bound_id = store.get_gateway_binding(platform, chat_id)
    session_id = bound_id or str(uuid.uuid4())
    session = factory(session_id=session_id)
    if bound_id:
        error = session.load_persisted(bound_id)
        if error:
            session = factory(session_id=str(uuid.uuid4()))
    _persist_gateway_session(store, session)
    store.set_gateway_binding(platform, chat_id, session.session_id)
    session_cache[key] = session
    return session


def reset_session(
    *,
    platform: str,
    chat_id: str,
    store: SessionStore,
    session_cache: dict[str, AgentSession],
    factory: Callable[..., AgentSession],
) -> AgentSession:
    """End the current chat session and start a fresh one."""
    key = cache_key(platform, chat_id)
    existing = session_cache.pop(key, None)
    if existing is not None:
        existing.end()
    store.clear_gateway_binding(platform, chat_id)
    session = factory(session_id=str(uuid.uuid4()))
    _persist_gateway_session(store, session)
    store.set_gateway_binding(platform, chat_id, session.session_id)
    session_cache[key] = session
    return session
