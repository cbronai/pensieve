/**
 * Pensieve Memory — Paperclip Plugin Worker
 *
 * Handles tool calls from agents, syncs episodic memory on turn events,
 * and exposes a read/archive API for the Paperclip board UI.
 */

import type { PluginWorkerContext, ToolCallRequest } from '@paperclipai/plugin-sdk';
import { manifest } from './manifest.js';

// ─── Schema shared with SDK core (replicated here to avoid Node.js dep) ───────

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT    NOT NULL,
    company_id TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    topic      TEXT,
    tier       TEXT    NOT NULL DEFAULT 'episodic',
    status     TEXT    NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_pm_agent   ON memories(agent_id);
  CREATE INDEX IF NOT EXISTS idx_pm_company ON memories(company_id);
  CREATE INDEX IF NOT EXISTS idx_pm_status  ON memories(status);
  CREATE INDEX IF NOT EXISTS idx_pm_topic   ON memories(topic);
`;

// ─── Plugin worker export ──────────────────────────────────────────────────────

export default {
  manifest,

  async onStart(ctx: PluginWorkerContext): Promise<void> {
    // Migrations run automatically from /migrations; this is just a log.
    ctx.logger.info('Pensieve memory plugin started');
  },

  // ── Tool dispatch ────────────────────────────────────────────────────────────

  async onToolCall(req: ToolCallRequest, ctx: PluginWorkerContext): Promise<string> {
    const agentId   = req.agentId;
    const companyId = req.companyId;
    const now       = Date.now();

    if (req.toolName === 'memory_store') {
      const { content, topic, tier = 'semantic' } = req.input as {
        content: string; topic?: string; tier?: string;
      };

      // Archive conflicts on same topic
      if (topic) {
        await ctx.db.execute(
          'UPDATE memories SET status=\'archived\', updated_at=? WHERE agent_id=? AND company_id=? AND topic=? AND status=\'active\'',
          [now, agentId, companyId, topic],
        );
      }

      const result = await ctx.db.execute(
        'INSERT INTO memories (agent_id,company_id,content,topic,tier,status,created_at,updated_at) VALUES (?,?,?,?,?,\'active\',?,?)',
        [agentId, companyId, content, topic ?? null, tier, now, now],
      );

      const topicStr = topic ? ` (topic: ${topic})` : '';
      return `Stored memory #${result.lastInsertRowid}${topicStr}.`;
    }

    if (req.toolName === 'memory_search') {
      const { query, max_results = 10, tier } = req.input as {
        query: string; max_results?: number; tier?: string;
      };

      const tierClause = tier ? 'AND tier=?' : '';
      const params     = tier
        ? [agentId, companyId, query, tier, max_results]
        : [agentId, companyId, query, max_results];

      const rows = await ctx.db.query<{ content: string; tier: string }>(
        `SELECT content, tier FROM memories
         WHERE agent_id=? AND company_id=? AND status='active' ${tierClause}
           AND content LIKE ?
         ORDER BY created_at DESC LIMIT ?`,
        params,
      );

      if (rows.length === 0) return 'No relevant memories found.';
      return rows.map((r) => `[${r.tier}] ${r.content}`).join('\n');
    }

    return `Unknown tool: ${req.toolName}`;
  },

  // ── Agent turn events — auto-sync episodic memory ────────────────────────────

  async onEvent(event: { type: string; payload: Record<string, unknown> }, ctx: PluginWorkerContext): Promise<void> {
    if (event.type !== 'agent.turn.completed') return;

    const { agentId, companyId, userMessage, assistantMessage } = event.payload as {
      agentId: string; companyId: string; userMessage: string; assistantMessage: string;
    };
    const now = Date.now();

    await ctx.db.execute(
      'INSERT INTO memories (agent_id,company_id,content,topic,tier,status,created_at,updated_at) VALUES (?,?,?,NULL,\'episodic\',\'active\',?,?)',
      [agentId, companyId, `User: ${userMessage}`, now, now],
    );
    await ctx.db.execute(
      'INSERT INTO memories (agent_id,company_id,content,topic,tier,status,created_at,updated_at) VALUES (?,?,?,NULL,\'episodic\',\'active\',?,?)',
      [agentId, companyId, `Assistant: ${assistantMessage}`, now, now],
    );
  },

  // ── Board/agent API routes ────────────────────────────────────────────────────

  async onApiRequest(
    req: { routeKey: string; params: Record<string, string>; companyId: string },
    ctx: PluginWorkerContext,
  ): Promise<unknown> {
    if (req.routeKey === 'list-memories') {
      return ctx.db.query(
        'SELECT id,content,topic,tier,status,created_at FROM memories WHERE agent_id=? AND company_id=? ORDER BY created_at DESC LIMIT 100',
        [req.params.agentId, req.companyId],
      );
    }

    if (req.routeKey === 'archive-memory') {
      const now = Date.now();
      await ctx.db.execute(
        'UPDATE memories SET status=\'archived\', updated_at=? WHERE id=? AND company_id=?',
        [now, req.params.memoryId, req.companyId],
      );
      return { archived: true };
    }

    return { error: 'Unknown route' };
  },
};
