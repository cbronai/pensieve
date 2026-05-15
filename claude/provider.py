"""
PensieveClaudeProvider — Pensieve memory for the Anthropic Claude API

Hierarchy:
  core      — permanent user identity facts
  semantic  — key facts learned across sessions
  episodic  — raw turn-by-turn conversation log
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── SQLite store (mirrors core TypeScript schema) ─────────────────────────────

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
        return rows or self._get_recent_raw(limit, tier)

    def _get_recent_raw(self, limit: int, tier: str | None) -> list[tuple]:
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
        core     = self._get_recent_raw(5, "core")
        semantic = (
            self.search(query, 5, "semantic") if query
            else [{"content": r[1]} for r in self._get_recent_raw(5, "semantic")]
        )
        episodic = self._get_recent_raw(5, "episodic")

        sections: list[str] = []
        if core:
            sections.append("## User Profile\n" + "\n".join(f"- {r[1]}" for r in core))
        if semantic:
            content_list = [m["content"] if isinstance(m, dict) else m[1] for m in semantic]
            sections.append("## Key Facts\n" + "\n".join(f"- {c}" for c in content_list))
        if episodic:
            sections.append("## Recent Context\n" + "\n".join(f"- {r[1]}" for r in episodic))
        return "\n\n".join(sections)

    def close(self) -> None:
        self._conn.close()


# ── Provider ──────────────────────────────────────────────────────────────────

_DEFAULT_MODEL = "claude-opus-4-6"

_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "memory_store",
        "description": (
            "Store a fact in Pensieve persistent memory. "
            "Provide a topic for automatic conflict resolution — "
            "a new fact on the same topic silently archives the old one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact or information to remember"},
                "topic":   {"type": "string", "description": "Semantic topic for conflict resolution"},
                "tier":    {"type": "string", "enum": ["episodic", "semantic", "core"],
                            "description": "Memory tier (default: semantic)"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search Pensieve persistent memory using full-text search. Returns relevant facts grouped by tier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string",  "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (default 10)"},
                "tier":        {"type": "string",  "enum": ["episodic", "semantic", "core"]},
            },
            "required": ["query"],
        },
    },
]


class PensieveClaudeProvider:
    """
    Pensieve memory provider for the Anthropic Claude API.

    Parameters
    ----------
    user_id : str
        Stable identifier for the user/agent whose memories are stored.
    db_path : str, optional
        SQLite database path. Defaults to ~/.pensieve/memory.db.
        Override with PENSIEVE_DB_PATH env var.
    model : str
        Claude model to use for the built-in `chat()` helper.
        Defaults to "claude-opus-4-6".
    """

    def __init__(
        self,
        user_id: str,
        db_path: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        resolved_path = (
            db_path
            or os.environ.get("PENSIEVE_DB_PATH")
            or str(Path.home() / ".pensieve" / "memory.db")
        )
        self._store = _SQLiteStore(resolved_path, user_id)
        self._user_id = user_id
        self._model = model
        logger.info("PensieveClaudeProvider initialised user=%s db=%s", user_id, resolved_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def tools(self) -> list[dict]:
        """Return the list of tool schemas to pass to `client.messages.create(tools=...)`."""
        return _TOOL_SCHEMAS

    def system_prompt(self, base: str | None = None) -> str:
        """
        Build a system prompt that injects the current memory context.

        Parameters
        ----------
        base : str, optional
            Your own system prompt. Memory block is appended after it.
        """
        memory_block = (
            "You have access to Pensieve — a persistent hierarchical memory.\n"
            "- Use `memory_store` to save important facts (with a topic for conflict resolution).\n"
            "- Use `memory_search` to recall relevant context before answering.\n\n"
        )
        context = self._store.get_context()
        if context:
            memory_block += context + "\n"

        return (base + "\n\n" + memory_block) if base else memory_block

    def handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch a tool call returned by Claude and return the result string."""
        if tool_name == "memory_store":
            mem_id = self._store.remember(
                tool_input["content"],
                topic=tool_input.get("topic"),
                tier=tool_input.get("tier", "semantic"),
            )
            suffix = f" (topic: {tool_input['topic']})" if tool_input.get("topic") else ""
            return f"Stored memory #{mem_id}{suffix}."

        if tool_name == "memory_search":
            results = self._store.search(
                tool_input["query"],
                limit=tool_input.get("max_results", 10),
                tier=tool_input.get("tier"),
            )
            if not results:
                return "No relevant memories found."
            return "\n".join(
                f"[{r['tier']}] {r['content']}" if isinstance(r, dict) else f"[{r[3]}] {r[1]}"
                for r in results
            )

        return f"Unknown memory tool: {tool_name}"

    def sync_turn(self, user_message: str, assistant_message: str) -> None:
        """Persist a completed turn as episodic memories (call after each exchange)."""
        self._store.remember(f"User: {user_message}", tier="episodic")
        self._store.remember(f"Assistant: {assistant_message}", tier="episodic")

    def chat(
        self,
        user_message: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        api_key: str | None = None,
    ) -> str:
        """
        Convenience helper: run a full memory-augmented turn with Claude.

        Handles the tool-use loop automatically — memory_store / memory_search
        calls are dispatched and the final text response is returned.

        Requires `anthropic` package: pip install anthropic
        """
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError("pip install anthropic") from exc

        client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        messages: list[dict] = [{"role": "user", "content": user_message}]

        while True:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=self.system_prompt(system),
                tools=self.tools(),  # type: ignore[arg-type]
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self.handle_tool_call(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # Final text response
            text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "",
            )
            self.sync_turn(user_message, text)
            return text

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._store.close()

    def __enter__(self) -> "PensieveClaudeProvider":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
