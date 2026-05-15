"""
PensieveMemory — CrewAI memory storage backed by Pensieve SQLite.

Implements the CrewAI Storage interface so it can be plugged in as
short-term, long-term, or entity memory for any CrewAI crew/agent.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

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

    def save(self, content: str, *, topic: str | None = None, tier: str = "episodic") -> int:
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

    def search(self, query: str, limit: int = 5) -> list[dict]:
        params: list[Any] = [query, self._user, limit]
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT m.id,m.content,m.topic,m.tier,m.created_at FROM memories m "
                    "JOIN memories_fts ON memories_fts.rowid=m.id "
                    "WHERE memories_fts MATCH ? AND m.user_id=? AND m.status='active' "
                    "ORDER BY rank LIMIT ?",
                    params,
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id,content,topic,tier,created_at FROM memories "
                    "WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT ?",
                    (self._user, limit),
                ).fetchall()
        return [{"id": r[0], "content": r[1], "topic": r[2], "tier": r[3], "created_at": r[4]}
                for r in rows]

    def reset(self) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET status='archived', updated_at=? "
                "WHERE user_id=? AND status='active'",
                (now, self._user),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ── CrewAI Storage adapter ────────────────────────────────────────────────────

try:
    from crewai.memory.storage.interface import Storage as _CrewAIStorage
    _BASE: type = _CrewAIStorage
    _CREWAI_AVAILABLE = True
except ImportError:
    class _CrewAIStorage:  # type: ignore[no-redef]
        pass
    _BASE = _CrewAIStorage
    _CREWAI_AVAILABLE = False


class PensieveMemory(_BASE):  # type: ignore[misc]
    """
    CrewAI-compatible memory storage backed by Pensieve's SQLite store.

    Use as short-term, long-term, or entity memory for CrewAI agents/crews.

    Parameters
    ----------
    user_id : str
        Stable identifier for the agent/crew instance.
    db_path : str, optional
        SQLite database path. Defaults to ~/.pensieve/memory.db.
    default_tier : str
        Tier for saves that don't specify one. Default: "semantic".
    """

    def __init__(
        self,
        user_id: str,
        db_path: str | None = None,
        default_tier: str = "semantic",
    ) -> None:
        resolved = (
            db_path
            or os.environ.get("PENSIEVE_DB_PATH")
            or str(Path.home() / ".pensieve" / "memory.db")
        )
        self._store = _SQLiteStore(resolved, user_id)
        self._user_id = user_id
        self._default_tier = default_tier

    # ── CrewAI Storage interface ──────────────────────────────────────────────

    def save(self, value: Any, metadata: dict | None = None, agent: str | None = None) -> None:  # type: ignore[override]
        """Persist a value. metadata['topic'] triggers conflict resolution."""
        content = str(value)
        topic = (metadata or {}).get("topic")
        tier  = (metadata or {}).get("tier", self._default_tier)
        self._store.save(content, topic=topic, tier=tier)

    def search(self, query: str) -> list[dict]:  # type: ignore[override]
        """Full-text search. Returns list of dicts with 'content', 'tier', 'topic'."""
        return self._store.search(query)

    def reset(self) -> None:
        """Archive all active memories (soft reset)."""
        self._store.reset()

    # ── Extra helpers ─────────────────────────────────────────────────────────

    def remember(self, content: str, *, topic: str | None = None, tier: str | None = None) -> int:
        """Convenience: store with explicit tier/topic."""
        return self._store.save(content, topic=topic, tier=tier or self._default_tier)

    def as_storage(self) -> "PensieveMemory":
        """Return self for use in memory_config={'storage': memory.as_storage()}."""
        return self

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "PensieveMemory":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
