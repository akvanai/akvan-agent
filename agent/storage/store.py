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
from agent.storage.permissions import (
    harden_session_db_files,
    is_under_akvan_home,
    prepare_akvan_parent,
)

SCHEMA_VERSION = 5
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

SCHEMA_V4_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content)
    VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
    INSERT INTO messages_fts(rowid, content)
    VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;
"""

SCHEMA_V5_SQL = """
DROP TRIGGER IF EXISTS messages_fts_delete;
DROP TRIGGER IF EXISTS messages_fts_update;

CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;

CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, content)
    VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;
"""

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
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=1.0,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if is_under_akvan_home(self._db_path):
            harden_session_db_files(self._db_path)
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
                    (1,),
                )
                self._conn.commit()
                current = 1
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
                current = 3
            if current < 4:
                self._conn.executescript(SCHEMA_V4_SQL)
                self._backfill_fts(self._conn)
                self._conn.execute(
                    "UPDATE schema_version SET version = ?",
                    (4,),
                )
                self._conn.commit()
                current = 4
            if current < 5:
                self._conn.executescript(SCHEMA_V5_SQL)
                self._conn.execute(
                    "UPDATE schema_version SET version = ?",
                    (5,),
                )
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def knowledge_review_batch(
        self,
        *,
        after_message_id: int,
        user_turn_limit: int,
    ) -> tuple[int, list[Message]] | None:
        """Return one persisted conversation batch ending at the Nth new user turn."""
        with self._lock:
            user_rows = self._conn.execute(
                """SELECT id FROM messages
                   WHERE id > ? AND role = 'user'
                   ORDER BY id ASC LIMIT ?""",
                (max(0, after_message_id), max(1, user_turn_limit)),
            ).fetchall()
            if len(user_rows) < max(1, user_turn_limit):
                return None
            high_water = int(user_rows[-1]["id"])
            rows = self._conn.execute(
                """SELECT id, session_id, role, content
                   FROM messages
                   WHERE id > ? AND id <= ? AND role IN ('user', 'assistant')
                   ORDER BY id ASC""",
                (max(0, after_message_id), high_water),
            ).fetchall()
        messages: list[Message] = []
        for row in rows:
            content = row["content"]
            if not isinstance(content, str) or not content.strip():
                continue
            messages.append(
                {
                    "role": row["role"],
                    "content": content,
                    "knowledge_message_id": str(row["id"]),
                    "knowledge_session_id": row["session_id"],
                }
            )
        return high_water, messages

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
        tool_name = message.get("tool_name") or message.get("name")
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

    def replace_messages(
        self,
        session_id: str,
        messages: list[Message],
    ) -> int:
        """Atomically replace a session transcript after context compaction."""

        encoded: list[tuple[object, ...]] = []
        now = time.time()
        for offset, message in enumerate(messages):
            role = message.get("role")
            if role == "system":
                continue
            if not isinstance(role, str):
                raise ValueError("message role must be a string")
            tool_call_id = message.get("tool_call_id")
            tool_name = message.get("tool_name") or message.get("name")
            tool_calls = message.get("tool_calls")
            reasoning = message.get("reasoning_content")
            encoded.append(
                (
                    session_id,
                    role,
                    self._encode_content(message.get("content")),
                    tool_call_id if isinstance(tool_call_id, str) else None,
                    tool_name if isinstance(tool_name, str) else None,
                    json.dumps(tool_calls) if tool_calls is not None else None,
                    reasoning if isinstance(reasoning, str) else None,
                    now + offset * 0.000001,
                )
            )

        def _write(conn: sqlite3.Connection) -> int:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            if encoded:
                conn.executemany(
                    """INSERT INTO messages
                       (session_id, role, content, tool_call_id, tool_name,
                        tool_calls, reasoning_content, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    encoded,
                )
            conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (len(encoded), session_id),
            )
            conn.commit()
            return len(encoded)

        return self._execute_write(_write)

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

    def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search across all session messages using FTS5."""
        sanitized = self._sanitize_fts_query(query)
        if not sanitized:
            return []
        params: list[object] = [sanitized, limit]
        exclude_clause = ""
        if exclude_session_id:
            exclude_clause = "AND m.session_id != ?"
            params.insert(1, exclude_session_id)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.id AS message_id, m.session_id, m.role, m.content,
                           m.tool_name, m.timestamp,
                           s.title, s.source, s.started_at,
                           snippet(messages_fts, 0, '>>>', '<<<', '...', 32) AS snippet,
                           rank
                    FROM messages_fts
                    JOIN messages m ON m.id = messages_fts.rowid
                    JOIN sessions s ON s.id = m.session_id
                    WHERE messages_fts MATCH ?
                    {exclude_clause}
                    ORDER BY rank
                    LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_messages_around(
        self,
        session_id: str,
        message_id: int,
        *,
        window: int = 5,
    ) -> list[dict[str, Any]]:
        """Return messages in a session centered on message_id."""
        with self._lock:
            anchor = self._conn.execute(
                """SELECT timestamp, id FROM messages
                   WHERE session_id = ? AND id = ?""",
                (session_id, message_id),
            ).fetchone()
            if anchor is None:
                return []
            rows = self._conn.execute(
                """SELECT id, session_id, role, content, tool_name, tool_calls,
                          tool_call_id, timestamp
                   FROM messages
                   WHERE session_id = ?
                     AND id BETWEEN ? AND ?
                   ORDER BY timestamp, id""",
                (
                    session_id,
                    max(1, message_id - window),
                    message_id + window,
                ),
            ).fetchall()
        results = []
        for row in rows:
            entry = dict(row)
            entry["anchor"] = entry["id"] == message_id
            if entry.get("tool_calls"):
                try:
                    entry["tool_calls"] = json.loads(entry["tool_calls"])
                except json.JSONDecodeError:
                    pass
            results.append(entry)
        return results

    def get_session_messages_with_ids(
        self, session_id: str, *, head: int = 20, tail: int = 10
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
        """Return session meta and messages (head+tail when large)."""
        with self._lock:
            meta_row = self._conn.execute(
                """SELECT id, title, source, model, started_at, message_count
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
            if meta_row is None:
                return None, [], False
            rows = self._conn.execute(
                """SELECT id, role, content, tool_name, tool_calls, tool_call_id, timestamp
                   FROM messages WHERE session_id = ?
                   ORDER BY timestamp, id""",
                (session_id,),
            ).fetchall()
        shaped = [dict(row) for row in rows]
        total = len(shaped)
        truncated = total > head + tail
        window = shaped[:head] + shaped[-tail:] if truncated else shaped
        return dict(meta_row), window, truncated

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        cleaned = query.strip()
        if not cleaned:
            return ""
        tokens = []
        for part in cleaned.split():
            token = "".join(ch for ch in part if ch.isalnum() or ch in {"_", "-"})
            if token:
                tokens.append(f'"{token}"*')
        return " ".join(tokens)

    @staticmethod
    def _backfill_fts(conn: sqlite3.Connection) -> None:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'messages_fts'"
        ).fetchone()
        if existing is None:
            return
        count = conn.execute("SELECT COUNT(*) AS n FROM messages_fts").fetchone()
        if count and int(count["n"]) > 0:
            return
        rows = conn.execute(
            """SELECT id, content, tool_name, tool_calls FROM messages"""
        ).fetchall()
        for row in rows:
            text = (
                f"{row['content'] or ''} {row['tool_name'] or ''} "
                f"{row['tool_calls'] or ''}"
            )
            conn.execute(
                "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                (row["id"], text),
            )

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
        prepare_akvan_parent(db_path)
        return SessionStore(db_path=db_path)
    except sqlite3.Error:
        return None
