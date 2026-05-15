"""
Pydantic AI tools for Pensieve memory.

Registers memory_store and memory_search as typed Pydantic AI tools
on any Agent instance, with dependency injection for multi-user support.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
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

    def remember(self, content: str, *, topic: str | None = None, tier: str = "semantic") -> int:
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
        tier_clause = "AND m.tier=?" if tier else ""
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
            recent_params: list[Any] = [self._user]
            if tier:
                recent_params.append(tier)
            recent_params.append(limit)
            tier_clause2 = "AND tier=?" if tier else ""
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT id,content,topic,tier FROM memories "
                    f"WHERE user_id=? AND status='active' {tier_clause2} "
                    f"ORDER BY created_at DESC LIMIT ?",
                    recent_params,
                ).fetchall()
        return [{"id": r[0], "content": r[1], "topic": r[2], "tier": r[3]} for r in rows]

    def close(self) -> None:
        self._conn.close()


# ── Dependency injection dataclass ────────────────────────────────────────────

@dataclass
class PensieveDeps:
    """
    Pydantic AI dependency for Pensieve memory.

    Inject into agent runs via `deps=PensieveDeps(user_id="alice")`.

    Attributes
    ----------
    user_id : str
        Stable identifier scoping all memories.
    db_path : str, optional
        SQLite path. Defaults to PENSIEVE_DB_PATH or ~/.pensieve/memory.db.
    """

    user_id: str
    db_path: str | None = None
    _store: _SQLiteStore | None = field(default=None, init=False, repr=False)

    def store(self) -> _SQLiteStore:
        if self._store is None:
            resolved = (
                self.db_path
                or os.environ.get("PENSIEVE_DB_PATH")
                or str(Path.home() / ".pensieve" / "memory.db")
            )
            self._store = _SQLiteStore(resolved, self.user_id)
        return self._store

    def close(self) -> None:
        if self._store:
            self._store.close()
            self._store = None


# ── Tool registration ─────────────────────────────────────────────────────────

def add_pensieve_tools(agent: Any) -> None:
    """
    Register memory_store and memory_search tools on a Pydantic AI Agent.

    Parameters
    ----------
    agent : pydantic_ai.Agent
        Any Pydantic AI agent instance with deps_type=PensieveDeps.
    """
    try:
        from pydantic_ai import RunContext  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("pip install pydantic-ai") from exc

    @agent.tool
    async def memory_store(
        ctx: RunContext[PensieveDeps],
        content: str,
        topic: str | None = None,
        tier: str = "semantic",
    ) -> str:
        """
        Store a fact in Pensieve persistent memory.
        Provide topic for automatic conflict resolution — new fact on same topic
        archives the old one.

        Args:
            content: The fact or information to remember.
            topic: Semantic topic for conflict resolution (optional).
            tier: Memory tier — 'episodic', 'semantic', or 'core'. Default: 'semantic'.
        """
        mem_id = ctx.deps.store().remember(content, topic=topic, tier=tier)
        suffix = f" (topic: {topic})" if topic else ""
        return f"Stored memory #{mem_id}{suffix}."

    @agent.tool
    async def memory_search(
        ctx: RunContext[PensieveDeps],
        query: str,
        max_results: int = 10,
        tier: str | None = None,
    ) -> str:
        """
        Search Pensieve persistent memory using full-text search.
        Returns relevant facts grouped by tier.

        Args:
            query: Search query.
            max_results: Maximum number of results to return (default 10).
            tier: Filter by tier — 'episodic', 'semantic', or 'core' (optional).
        """
        results = ctx.deps.store().search(query, limit=max_results, tier=tier)
        if not results:
            return "No relevant memories found."
        return "\n".join(f"[{r['tier']}] {r['content']}" for r in results)


def create_pensieve_agent(
    model: str,
    user_id: str,
    db_path: str | None = None,
    system_prompt: str = "You are a helpful assistant with persistent hierarchical memory.",
    **agent_kwargs: Any,
) -> Any:
    """
    Create a Pydantic AI Agent pre-wired with Pensieve memory tools.

    Parameters
    ----------
    model : str
        Pydantic AI model string, e.g. 'claude-opus-4-6' or 'openai:gpt-4o'.
    user_id : str
        Stable user identifier for memory scoping.
    db_path : str, optional
        SQLite database path.
    system_prompt : str
        Agent system prompt.
    **agent_kwargs
        Extra kwargs forwarded to pydantic_ai.Agent.

    Returns
    -------
    pydantic_ai.Agent
        Agent with memory_store and memory_search tools registered.
        Run with: agent.run_sync("...", deps=PensieveDeps(user_id=user_id))
    """
    try:
        from pydantic_ai import Agent  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("pip install pydantic-ai") from exc

    memory_block = (
        "\n\nYou have access to Pensieve — a persistent hierarchical memory.\n"
        "- Use memory_store to save important facts (with topic for conflict resolution).\n"
        "- Use memory_search to recall relevant context before answering."
    )

    agent: Any = Agent(
        model,
        deps_type=PensieveDeps,
        system_prompt=system_prompt + memory_block,
        **agent_kwargs,
    )
    add_pensieve_tools(agent)
    return agent
