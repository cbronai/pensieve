"""
PensieveMemory — LangChain BaseChatMemory backed by Pensieve SQLite store.

Implements both the classic BaseChatMemory interface (ConversationChain) and
the newer BaseChatMessageHistory interface (LCEL / RunnableWithMessageHistory).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Sequence

# ── SQLite store ──────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    topic      TEXT,
    tier       TEXT    NOT NULL DEFAULT 'episodic',
    status     TEXT    NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user   ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_tier   ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_mem_topic  ON memories(topic);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, topic, content=memories, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, topic) VALUES (new.id, new.content, new.topic);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, topic)
    VALUES ('delete', old.id, old.content, old.topic);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, topic)
    VALUES ('delete', old.id, old.content, old.topic);
    INSERT INTO memories_fts(rowid, content, topic) VALUES (new.id, new.content, new.topic);
END;
"""


class _SQLiteStore:
    def __init__(self, db_path: str, user_id: str) -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self._user = user_id

    def remember(self, content: str, *, topic: str | None = None, tier: str = "episodic") -> int:
        now = int(time.time() * 1000)
        with self._lock:
            if topic:
                self._conn.execute(
                    "UPDATE memories SET status='archived', updated_at=? "
                    "WHERE user_id=? AND topic=? AND status='active'",
                    (now, self._user, topic),
                )
            cur = self._conn.execute(
                "INSERT INTO memories (user_id,content,topic,tier,status,created_at,updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (self._user, content, topic, tier, now, now),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_recent_episodic(self, limit: int = 20) -> list[tuple[str, str]]:
        """Return recent episodic rows as (role, content) pairs."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT content FROM memories "
                "WHERE user_id=? AND status='active' AND tier='episodic' "
                "ORDER BY created_at DESC LIMIT ?",
                (self._user, limit),
            ).fetchall()
        # Stored as "User: ..." / "Assistant: ..." — parse back to (role, text)
        pairs: list[tuple[str, str]] = []
        for (raw,) in reversed(rows):
            if raw.startswith("User: "):
                pairs.append(("human", raw[len("User: "):]))
            elif raw.startswith("Assistant: "):
                pairs.append(("ai", raw[len("Assistant: "):]))
        return pairs

    def get_context(self, query: str | None = None) -> str:
        def recent(limit: int, tier: str) -> list[str]:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT content FROM memories "
                    "WHERE user_id=? AND status='active' AND tier=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (self._user, tier, limit),
                ).fetchall()
            return [r[0] for r in rows]

        core = recent(5, "core")
        semantic = recent(5, "semantic")
        episodic_raw = recent(5, "episodic")
        sections: list[str] = []
        if core:
            sections.append("## User Profile\n" + "\n".join(f"- {c}" for c in core))
        if semantic:
            sections.append("## Key Facts\n" + "\n".join(f"- {c}" for c in semantic))
        if episodic_raw:
            sections.append("## Recent Context\n" + "\n".join(f"- {c}" for c in episodic_raw))
        return "\n\n".join(sections)

    def clear_episodic(self) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET status='archived', updated_at=? "
                "WHERE user_id=? AND tier='episodic' AND status='active'",
                (now, self._user),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ── PensieveMemory ─────────────────────────────────────────────────────────────

try:
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    # Provide stub bases so the class can be defined without langchain installed
    class BaseChatMessageHistory:  # type: ignore[no-redef]
        pass
    class BaseMessage:  # type: ignore[no-redef]
        pass
    class HumanMessage:  # type: ignore[no-redef]
        def __init__(self, content: str) -> None:
            self.content = content
    class AIMessage:  # type: ignore[no-redef]
        def __init__(self, content: str) -> None:
            self.content = content


class PensieveMemory(BaseChatMessageHistory):
    """
    LangChain chat message history backed by Pensieve's SQLite store.

    Compatible with both ConversationChain (classic) and
    RunnableWithMessageHistory (LCEL).

    Parameters
    ----------
    user_id : str
        Stable identifier scoping all memories.
    db_path : str, optional
        SQLite database path. Defaults to ~/.pensieve/memory.db.
    history_limit : int
        Number of recent episodic turns to surface as message history.
    memory_key : str
        Key injected into chain inputs (classic ConversationChain use).
    """

    def __init__(
        self,
        user_id: str,
        db_path: str | None = None,
        history_limit: int = 10,
        memory_key: str = "history",
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError("pip install langchain-core")
        resolved = (
            db_path
            or os.environ.get("PENSIEVE_DB_PATH")
            or str(Path.home() / ".pensieve" / "memory.db")
        )
        self._store = _SQLiteStore(resolved, user_id)
        self._user_id = user_id
        self._history_limit = history_limit
        self.memory_key = memory_key

    # ── BaseChatMessageHistory interface ──────────────────────────────────────

    @property
    def messages(self) -> list[BaseMessage]:
        pairs = self._store.get_recent_episodic(self._history_limit * 2)
        result: list[BaseMessage] = []
        for role, content in pairs:
            if role == "human":
                result.append(HumanMessage(content=content))
            else:
                result.append(AIMessage(content=content))
        return result

    def add_message(self, message: BaseMessage) -> None:
        if isinstance(message, HumanMessage):
            self._store.remember(f"User: {message.content}", tier="episodic")
        elif isinstance(message, AIMessage):
            self._store.remember(f"Assistant: {message.content}", tier="episodic")

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        for msg in messages:
            self.add_message(msg)

    def clear(self) -> None:
        """Archive all episodic memories (does not touch semantic/core)."""
        self._store.clear_episodic()

    # ── Semantic/core helpers ─────────────────────────────────────────────────

    def remember(self, content: str, *, topic: str | None = None, tier: str = "semantic") -> int:
        """Store a semantic or core fact directly."""
        return self._store.remember(content, topic=topic, tier=tier)

    def get_context(self, query: str | None = None) -> str:
        """Get formatted memory context for manual system prompt injection."""
        return self._store.get_context(query)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "PensieveMemory":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
