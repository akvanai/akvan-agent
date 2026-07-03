"""
SQLite session store for Akvan Agent.

Persists session metadata and message history to ~/.akvan/state.db.
Phase 1: create, sync, list, resume, and end sessions.

Future phases (not implemented — stubs documented below):
  Phase 2: FTS5 full-text search across message history
  Phase 3: per-session token and cost tracking
  Phase 4: session lineage via parent_session_id (compression splits)
  Phase 5: auto-prune ended sessions and VACUUM
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agent.config import state_db_path
from agent.messages import Message

SCHEMA_VERSION = 3
SESSION_PAGE_SIZE = 15

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    model TEXT,
    provider TEXT,
    cwd TEXT,
    title TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    tool_calls TEXT,
    reasoning_content TEXT,
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS gateway_bindings (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id)
);

CREATE TABLE IF NOT EXISTS gateway_preferences (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    model TEXT,
    approval_mode TEXT,
    stream_transport TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id)
);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS gateway_bindings (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id)
);
"""

SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS gateway_preferences (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    model TEXT,
    approval_mode TEXT,
    stream_transport TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id)
);
"""

# --- Phase 2: FTS5 full-text search ---
# CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
#     content,
#     content=messages,
#     content_rowid=id
# );
# CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
#     INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
# END;
#
# def search_messages(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
#     """Full-text search across all session messages using FTS5."""
#     ...

# --- Phase 3: usage and billing ---
# ALTER TABLE sessions ADD COLUMN input_tokens INTEGER DEFAULT 0;
# ALTER TABLE sessions ADD COLUMN output_tokens INTEGER DEFAULT 0;
# ALTER TABLE sessions ADD COLUMN estimated_cost_usd REAL;
#
# def update_session_usage(
#     self, session_id: str, *, input_tokens: int, output_tokens: int, cost_usd: float
# ) -> None:
#     ...

# --- Phase 4: session lineage ---
# ALTER TABLE sessions ADD COLUMN parent_session_id TEXT;
# ALTER TABLE sessions ADD COLUMN end_reason TEXT;
#
# def create_child_session(
#     self, parent_id: str, *, end_reason: str = "compression"
# ) -> str:
#     ...

# --- Phase 5: auto-prune ---
# CREATE TABLE IF NOT EXISTS state_meta (
#     key TEXT PRIMARY KEY,
#     value TEXT
# );
#
# def prune_sessions(self, older_than_days: int = 90) -> int:
#     """Delete ended sessions older than the retention window."""
#     ...
#
# def vacuum(self) -> None:
#     """Reclaim disk space after a prune sweep."""
#     ...


class SessionStore:
    """SQLite-backed session storage with WAL mode."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or state_db_path()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), timeout=1.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            row = self._conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                self._conn.commit()
            else:
                current = int(row["version"])
                if current < 2:
                    self._conn.executescript(SCHEMA_V2_SQL)
                    self._conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (2,),
                    )
                    current = 2
                if current < 3:
                    self._conn.executescript(SCHEMA_V3_SQL)
                    self._conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (3,),
                    )
                    self._conn.commit()

                    self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def create_session(
        self,
        session_id: str,
        *,
        source: str,
        model: str | None = None,
        provider: str | None = None,
        cwd: str | None = None,
    ) -> str:
        """Create a new session row. Returns the session id."""
        now = time.time()

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO sessions
                   (id, source, model, provider, cwd, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, source, model, provider, cwd, now),
            )
            conn.commit()

        self._execute_write(_write)
        return session_id

    def session_exists(self, session_id: str) -> bool:
        """Return True when a session row already exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return row is not None

    def end_session(self, session_id: str, *, reason: str = "exit") -> None:
        """Mark a session as ended. No-op if already ended."""
        now = time.time()

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (now, session_id),
            )
            conn.commit()

        self._execute_write(_write)
        _ = reason  # reserved for Phase 4 end_reason column

    def append_message(self, session_id: str, message: Message) -> int:
        """Append one message row. Returns the SQLite row id."""
        role = message.get("role")
        if not isinstance(role, str):
            raise ValueError("message role must be a string")

        content = self._encode_content(message.get("content"))
        tool_call_id = message.get("tool_call_id")
        tool_name = message.get("tool_name")
        tool_calls = message.get("tool_calls")
        tool_calls_json = json.dumps(tool_calls) if tool_calls is not None else None
        reasoning = message.get("reasoning_content")
        reasoning_str = reasoning if isinstance(reasoning, str) else None
        now = time.time()

        if not isinstance(tool_call_id, str):
            tool_call_id = None
        if not isinstance(tool_name, str):
            tool_name = None

        def _write(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_call_id, tool_name,
                    tool_calls, reasoning_content, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    tool_call_id,
                    tool_name,
                    tool_calls_json,
                    reasoning_str,
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                (session_id,),
            )
            self._maybe_set_title(conn, session_id, role, content)
            conn.commit()
            return int(cursor.lastrowid)

        return self._execute_write(_write)

    def sync_messages(
        self,
        session_id: str,
        messages: list[Message],
        *,
        start_index: int = 0,
    ) -> int:
        """Flush messages[start_index:] to the database. Returns rows written."""
        written = 0
        for index in range(start_index, len(messages)):
            message = messages[index]
            role = message.get("role")
            if role == "system":
                continue
            self.append_message(session_id, message)
            written += 1
        return written

    def get_messages(self, session_id: str) -> list[Message]:
        """Load all messages for a session in conversation order."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT role, content, tool_call_id, tool_name, tool_calls,
                          reasoning_content
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY timestamp, id""",
                (session_id,),
            ).fetchall()

        return [self._row_to_message(row) for row in rows]

    def count_sessions(self, *, exclude_session_id: str | None = None) -> int:
        """Return the total number of saved sessions."""
        if exclude_session_id:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE id != ?",
                    (exclude_session_id,),
                ).fetchone()
        else:
            with self._lock:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row is not None else 0

    def list_sessions(
        self,
        *,
        limit: int = SESSION_PAGE_SIZE,
        offset: int = 0,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent sessions with a first-user-message preview."""
        where_clause = ""
        params: list[object] = []
        if exclude_session_id:
            where_clause = "WHERE s.id != ?"
            params.append(exclude_session_id)

        with self._lock:
            rows = self._conn.execute(
                f"""SELECT s.id, s.title, s.model, s.provider, s.cwd,
                          s.started_at, s.ended_at, s.message_count,
                          COALESCE(
                              (SELECT SUBSTR(m.content, 1, 80)
                               FROM messages m
                               WHERE m.session_id = s.id
                                 AND m.role = 'user'
                                 AND m.content IS NOT NULL
                               ORDER BY m.timestamp, m.id
                               LIMIT 1),
                              ''
                          ) AS preview
                   FROM sessions s
                   {where_clause}
                   ORDER BY s.started_at DESC
                   LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()

        return [dict(row) for row in rows]

    def ensure_session_exists(
        self,
        session_id: str,
        *,
        source: str,
        model: str | None = None,
        provider: str | None = None,
        cwd: str | None = None,
    ) -> None:
        """Create a session row when missing (for gateway bindings)."""
        if self.session_exists(session_id):
            return
        self.create_session(
            session_id,
            source=source,
            model=model,
            provider=provider,
            cwd=cwd,
        )

    def get_gateway_binding(self, platform: str, chat_id: str) -> str | None:
        """Return the session id bound to a gateway chat, if any."""
        with self._lock:
            row = self._conn.execute(
                """SELECT session_id FROM gateway_bindings
                   WHERE platform = ? AND chat_id = ?""",
                (platform, chat_id),
            ).fetchone()
        return str(row["session_id"]) if row is not None else None

    def set_gateway_binding(
        self, platform: str, chat_id: str, session_id: str
    ) -> None:
        """Bind a gateway chat to an Akvan session id."""
        now = time.time()

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO gateway_bindings
                   (platform, chat_id, session_id, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(platform, chat_id) DO UPDATE SET
                     session_id = excluded.session_id,
                     updated_at = excluded.updated_at""",
                (platform, chat_id, session_id, now),
            )
            conn.commit()

        self._execute_write(_write)

    def clear_gateway_binding(self, platform: str, chat_id: str) -> None:
        """Remove a gateway chat binding."""

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM gateway_bindings WHERE platform = ? AND chat_id = ?",
                (platform, chat_id),
            )
            conn.commit()

        self._execute_write(_write)

    def get_gateway_preferences(self, platform: str, chat_id: str) -> dict[str, str]:
        """Return non-empty chat-scoped gateway preferences."""
        with self._lock:
            row = self._conn.execute(
                """SELECT model, approval_mode, stream_transport
                   FROM gateway_preferences
                   WHERE platform = ? AND chat_id = ?""",
                (platform, chat_id),
            ).fetchone()
        if row is None:
            return {}
        return {
            key: str(row[key])
            for key in ("model", "approval_mode", "stream_transport")
            if row[key]
        }

    def set_gateway_preferences(
        self, platform: str, chat_id: str, *, model: str | None = None,
        approval_mode: str | None = None, stream_transport: str | None = None,
    ) -> None:
        """Upsert chat preferences while preserving unspecified values."""
        now = time.time()

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO gateway_preferences
                   (platform, chat_id, model, approval_mode, stream_transport, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, chat_id) DO UPDATE SET
                     model = COALESCE(excluded.model, gateway_preferences.model),
                     approval_mode = COALESCE(excluded.approval_mode, gateway_preferences.approval_mode),
                     stream_transport = COALESCE(excluded.stream_transport, gateway_preferences.stream_transport),
                     updated_at = excluded.updated_at""",
                (platform, chat_id, model, approval_mode, stream_transport, now),
            )
            conn.commit()

        self._execute_write(_write)

    def update_session_model(self, session_id: str, model: str) -> None:
        """Update model metadata for a live session."""
        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE sessions SET model = ? WHERE id = ?", (model, session_id))
            conn.commit()

        self._execute_write(_write)

    def resolve_session_id(self, target: str) -> str | None:
        """Resolve a session id from a full/prefix id or exact title."""
        target = target.strip()
        if not target:
            return None

        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?",
                (target,),
            ).fetchone()
            if row is not None:
                return str(row["id"])

            row = self._conn.execute(
                """SELECT id FROM sessions
                   WHERE id LIKE ? ESCAPE '\\'
                   ORDER BY started_at DESC
                   LIMIT 1""",
                (self._escape_like(target) + "%",),
            ).fetchone()
            if row is not None:
                return str(row["id"])

            row = self._conn.execute(
                """SELECT id FROM sessions
                   WHERE title = ?
                   ORDER BY started_at DESC
                   LIMIT 1""",
                (target,),
            ).fetchone()
            if row is not None:
                return str(row["id"])

        return None

    def _execute_write(self, fn):
        with self._lock:
            return fn(self._conn)

    @staticmethod
    def _encode_content(content: object | None) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        return json.dumps(content)

    @staticmethod
    def _decode_content(content: str | None) -> object | None:
        if content is None:
            return None
        if content.startswith(("[", "{")):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                pass
        return content

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        message: Message = {
            "role": row["role"],
            "content": self._decode_content(row["content"]),
        }
        if row["tool_call_id"]:
            message["tool_call_id"] = row["tool_call_id"]
        if row["tool_name"]:
            message["tool_name"] = row["tool_name"]
        if row["tool_calls"]:
            message["tool_calls"] = json.loads(row["tool_calls"])
        if row["reasoning_content"]:
            message["reasoning_content"] = row["reasoning_content"]
        return message

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _maybe_set_title(
        conn: sqlite3.Connection,
        session_id: str,
        role: str,
        content: str | None,
    ) -> None:
        if role != "user" or not content:
            return
        preview = content.strip()
        if not preview:
            return
        title = preview if len(preview) <= 80 else preview[:77] + "..."
        conn.execute(
            """UPDATE sessions SET title = ?
               WHERE id = ? AND title IS NULL""",
            (title, session_id),
        )


def open_session_store(db_path: Path | None = None) -> SessionStore | None:
    """Open the session store, returning None if SQLite initialization fails."""
    try:
        db_path = db_path or state_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SessionStore(db_path=db_path)
    except sqlite3.Error:
        return None
