"""
PensieveMemoryProvider — Hermes MemoryProvider implementation

Plugs into Hermes Agent's memory provider slot to replace the built-in
file-based memory with Pensieve's SQLite-backed hierarchical store.

Hierarchy:
  core      — permanent user identity facts (preferences, name, etc.)
  semantic  — key facts learned across sessions (resolved conflicts)
  episodic  — raw turn-by-turn conversation log (recent context)

Conflict resolution:
  When a new fact arrives with the same topic as an existing one, the
  old memory is automatically archived — no stale data leaks into context.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Inline SQLite store (mirrors sdk/core TypeScript schema exactly)
# ──────────────────────────────────────────────────────────────────────────────

CREATE_SCHEMA = """
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
    content,
    topic,
    content=memories,
    content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, topic)
    VALUES (new.id, new.content, new.topic);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, topic)
    VALUES ('delete', old.id, old.content, old.topic);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, topic)
    VALUES ('delete', old.id, old.content, old.topic);
    INSERT INTO memories_fts(rowid, content, topic)
    VALUES (new.id, new.content, new.topic);
END;
"""


class _SQLiteStore:
    def __init__(self, db_path: str, user_id: str) -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(CREATE_SCHEMA)
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

    def search(self, query: str, limit: int = 10, tier: str | None = None) -> list[dict]:
        tier_clause = "AND m.tier = ?" if tier else ""
        params: list[Any] = [query, self._user]
        if tier:
            params.append(tier)
        params.append(limit)
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT m.id,m.content,m.topic,m.tier FROM memories m "
                    f"JOIN memories_fts ON memories_fts.rowid=m.id "
                    f"WHERE memories_fts MATCH ? AND m.user_id=? AND m.status='active' "
                    f"{tier_clause} ORDER BY rank LIMIT ?",
                    params,
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            rows = self._get_recent(limit, tier)
        return [{"id": r[0], "content": r[1], "topic": r[2], "tier": r[3]} for r in rows]

    def _get_recent(self, limit: int = 10, tier: str | None = None) -> list[tuple]:
        tier_clause = "AND tier=?" if tier else ""
        params: list[Any] = [self._user]
        if tier:
            params.append(tier)
        params.append(limit)
        with self._lock:
            return self._conn.execute(
                f"SELECT id,content,topic,tier FROM memories "
                f"WHERE user_id=? AND status='active' {tier_clause} "
                f"ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()

    def get_context(self, query: str | None = None) -> str:
        core     = self._get_recent(5, "core")
        semantic = self.search(query, 5, "semantic") if query else [
            {"content": r[1], "tier": r[3]} for r in self._get_recent(5, "semantic")
        ]
        episodic = self._get_recent(5, "episodic")

        sections: list[str] = []
        if core:
            sections.append("## User Profile\n" + "\n".join(f"- {r[1]}" for r in core))
        if semantic:
            sections.append("## Key Facts\n" + "\n".join(f"- {m['content']}" for m in semantic))
        if episodic:
            sections.append("## Recent Context\n" + "\n".join(f"- {r[1]}" for r in episodic))
        return "\n\n".join(sections)

    def close(self) -> None:
        self._conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Hermes MemoryProvider implementation
# ──────────────────────────────────────────────────────────────────────────────

try:
    from agent.memory_provider import MemoryProvider as _HermesMemoryProvider
    _BASE = _HermesMemoryProvider
except ImportError:
    # Running outside Hermes (e.g. tests, standalone) — use a plain base
    class _HermesMemoryProvider:  # type: ignore[no-redef]
        pass
    _BASE = _HermesMemoryProvider


class PensieveMemoryProvider(_BASE):  # type: ignore[misc]
    """Hermes MemoryProvider backed by Pensieve's SQLite store."""

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "pensieve"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return True  # No external deps — just SQLite

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        hermes_home: str = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        user_id: str = kwargs.get("user_id") or kwargs.get("agent_identity") or session_id

        db_path = os.environ.get(
            "PENSIEVE_DB_PATH",
            str(Path(hermes_home) / "pensieve" / "memory.db"),
        )
        self._store = _SQLiteStore(db_path, user_id)
        self._session_id = session_id
        self._prefetch_cache: dict[str, str] = {}
        self._prefetch_lock = threading.Lock()
        logger.info("Pensieve memory initialized for user=%s db=%s", user_id, db_path)

    # ── System prompt ─────────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        return (
            "You have a persistent hierarchical memory (Pensieve). "
            "Use memory_store to save important facts with a topic for conflict resolution. "
            "Use memory_search to recall relevant context before answering."
        )

    # ── Per-turn recall ───────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            cached = self._prefetch_cache.get(session_id or self._session_id, "")
        return cached

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Background recall — result consumed by next prefetch() call."""
        def _run() -> None:
            context = self._store.get_context(query)
            key = session_id or self._session_id
            with self._prefetch_lock:
                self._prefetch_cache[key] = context

        threading.Thread(target=_run, daemon=True).start()

    # ── Post-turn sync ────────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist the raw turn as episodic memory in background."""
        def _run() -> None:
            self._store.remember(f"User: {user_content}", tier="episodic")
            self._store.remember(f"Assistant: {assistant_content}", tier="episodic")

        threading.Thread(target=_run, daemon=True).start()

    # ── Tool schemas ──────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_store",
                "description": (
                    "Store a fact in persistent memory. "
                    "Provide a topic to enable automatic conflict resolution — "
                    "a new fact on the same topic silently archives the old one."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The fact or information to remember"},
                        "topic":   {"type": "string", "description": "Semantic topic for conflict resolution"},
                        "tier":    {"type": "string", "enum": ["episodic", "semantic", "core"]},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "Search persistent memory using full-text search. Returns relevant facts and context.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query":      {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Max results (default 10)"},
                        "tier":       {"type": "string", "enum": ["episodic", "semantic", "core"]},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "memory_store":
            mem_id = self._store.remember(
                tool_input["content"],
                topic=tool_input.get("topic"),
                tier=tool_input.get("tier", "semantic"),
            )
            topic_str = f" (topic: {tool_input['topic']})" if tool_input.get("topic") else ""
            return f"Stored memory #{mem_id}{topic_str}."

        if tool_name == "memory_search":
            results = self._store.search(
                tool_input["query"],
                limit=tool_input.get("max_results", 10),
                tier=tool_input.get("tier"),
            )
            if not results:
                return "No relevant memories found."
            return "\n".join(f"[{m['tier']}] {m['content']}" for m in results)

        return f"Unknown memory tool: {tool_name}"

    # ── Optional hooks ────────────────────────────────────────────────────────

    def on_session_end(self, messages: list) -> None:
        """Flush recent episodic turns; close DB cleanly."""
        try:
            self._store.close()
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self._store.close()
        except Exception:
            pass
