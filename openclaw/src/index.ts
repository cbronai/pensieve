/**
 * @pensieve/openclaw — OpenClaw memory slot plugin
 *
 * Replaces OpenClaw's built-in memory-core / memory-lancedb with Pensieve's
 * SQLite-backed hierarchical store. Exposes the standard OpenClaw memory tools:
 *   memory_search, memory_get, memory_store, memory_forget
 *
 * Install:
 *   openclaw plugin add @pensieve/openclaw
 *
 * Config in ~/.openclaw/config.yaml:
 *   plugins:
 *     slots:
 *       memory: pensieve-memory
 *     entries:
 *       pensieve-memory:
 *         dbPath: ~/.pensieve/openclaw.db
 *         autoResolveConflicts: true
 */

import os from 'node:os';
import path from 'node:path';
import { PensieveLocal, type MemoryTier } from '@cbronai/pensieve-local';
import { definePluginEntry, type OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';
import { resolveLivePluginConfigObject } from 'openclaw/plugin-sdk/plugin-config-runtime';

// ─── Per-agent Pensieve instances ────────────────────────────────────────────

const instances = new Map<string, PensieveLocal>();

function getInstance(agentId: string, dbPath: string, autoResolveConflicts: boolean): PensieveLocal {
  if (!instances.has(agentId)) {
    instances.set(agentId, new PensieveLocal({ userId: agentId, dbPath, autoResolveConflicts }));
  }
  return instances.get(agentId)!;
}

function resolveDbPath(raw?: string): string {
  const p = raw ?? '~/.pensieve/openclaw.db';
  return path.resolve(p.replace(/^~/, os.homedir()));
}

// ─── Plugin entry ─────────────────────────────────────────────────────────────

export default definePluginEntry({
  tools: {
    memory_search: {
      description: 'Search agent memories using full-text search (FTS5). Returns relevant facts and context.',
      inputSchema: {
        type: 'object' as const,
        properties: {
          query:      { type: 'string', description: 'Search query' },
          maxResults: { type: 'number', description: 'Max results to return (default 10)' },
          tier:       { type: 'string', enum: ['episodic', 'semantic', 'core', 'all'] as const },
        },
        required: ['query'] as const,
        additionalProperties: false,
      },
      async execute(input, ctx: OpenClawPluginApi) {
        const cfg  = resolveLivePluginConfigObject(ctx.config, 'pensieve-memory') ?? {};
        const db   = getInstance(ctx.agentId, resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
        const tier = input.tier === 'all' ? undefined : (input.tier as MemoryTier | undefined);
        const mems = db.recall(input.query, { limit: input.maxResults ?? 10, tier });
        if (mems.length === 0) return 'No relevant memories found.';
        return mems.map(m => `[${m.tier}] ${m.content}`).join('\n');
      },
    },

    memory_get: {
      description: 'Get the full formatted memory context for the current agent (all tiers).',
      inputSchema: {
        type: 'object' as const,
        properties: {
          query: { type: 'string', description: 'Optional topic to focus context retrieval' },
        },
        additionalProperties: false,
      },
      async execute(input, ctx: OpenClawPluginApi) {
        const cfg = resolveLivePluginConfigObject(ctx.config, 'pensieve-memory') ?? {};
        const db  = getInstance(ctx.agentId, resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
        return db.getContext(input.query) || 'No memories stored yet.';
      },
    },

    memory_store: {
      description: 'Store a new memory. Set topic for conflict resolution — storing a fact on the same topic archives the old one.',
      inputSchema: {
        type: 'object' as const,
        properties: {
          content: { type: 'string', description: 'The memory to store' },
          topic:   { type: 'string', description: 'Semantic topic (enables conflict resolution)' },
          tier:    { type: 'string', enum: ['episodic', 'semantic', 'core'] as const },
        },
        required: ['content'] as const,
        additionalProperties: false,
      },
      async execute(input, ctx: OpenClawPluginApi) {
        const cfg = resolveLivePluginConfigObject(ctx.config, 'pensieve-memory') ?? {};
        const db  = getInstance(ctx.agentId, resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
        const mem = db.remember(input.content, {
          topic: input.topic,
          tier:  (input.tier as MemoryTier) ?? (cfg.defaultTier as MemoryTier) ?? 'semantic',
        });
        return `Stored memory #${mem.id}${input.topic ? ` (topic: ${input.topic})` : ''}.`;
      },
    },

    memory_forget: {
      description: 'Archive (soft-delete) memories matching a topic or query.',
      inputSchema: {
        type: 'object' as const,
        properties: {
          topic: { type: 'string', description: 'Exact topic to archive' },
        },
        required: ['topic'] as const,
        additionalProperties: false,
      },
      async execute(input, ctx: OpenClawPluginApi) {
        const cfg = resolveLivePluginConfigObject(ctx.config, 'pensieve-memory') ?? {};
        const db  = getInstance(ctx.agentId, resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
        // Re-remember with empty content triggers archiving of old topic
        db.remember('', { topic: input.topic, resolveConflicts: true });
        return `Archived all memories for topic: ${input.topic}.`;
      },
    },
  },
} satisfies Parameters<typeof definePluginEntry>[0]);
