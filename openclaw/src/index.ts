/**
 * @cbronai/pensieve-openclaw — OpenClaw memory slot plugin
 *
 * Replaces OpenClaw's built-in memory with Pensieve's SQLite-backed
 * hierarchical store. Exposes the standard memory tools:
 *   memory_search, memory_get, memory_store, memory_forget
 *
 * Install:
 *   openclaw plugin add @cbronai/pensieve-openclaw
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

// ─── Per-agent Pensieve instances ─────────────────────────────────────────────

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

function resolveConfig(ctx: { config?: unknown }): { dbPath?: string; autoResolveConflicts?: boolean } {
  if (!ctx.config || typeof ctx.config !== 'object') return {};
  const cfg = ctx.config as Record<string, unknown>;
  // Support both direct config object and nested plugin config
  const entry = (cfg['pensieve-memory'] ?? cfg) as Record<string, unknown>;
  return {
    dbPath: typeof entry['dbPath'] === 'string' ? entry['dbPath'] : undefined,
    autoResolveConflicts: typeof entry['autoResolveConflicts'] === 'boolean'
      ? entry['autoResolveConflicts']
      : true,
  };
}

function resolveAgentId(ctx: { agent?: unknown; agentId?: unknown }): string {
  const id = ctx.agent ?? ctx.agentId;
  return typeof id === 'string' ? id : 'default';
}

// ─── Tool handlers ────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type PluginCtx = Record<string, any>;

export function handleMemorySearch(input: Record<string, unknown>, ctx: PluginCtx): string {
  const cfg    = resolveConfig(ctx);
  const db     = getInstance(resolveAgentId(ctx), resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
  const tier   = input['tier'] === 'all' ? undefined : (input['tier'] as MemoryTier | undefined);
  const limit  = typeof input['maxResults'] === 'number' ? input['maxResults'] : 10;
  const query  = String(input['query'] ?? '');
  const mems   = db.recall(query, { limit, tier });
  if (mems.length === 0) return 'No relevant memories found.';
  return mems.map((m: { tier: string; content: string }) => `[${m.tier}] ${m.content}`).join('\n');
}

export function handleMemoryGet(input: Record<string, unknown>, ctx: PluginCtx): string {
  const cfg = resolveConfig(ctx);
  const db  = getInstance(resolveAgentId(ctx), resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
  return db.getContext(typeof input['query'] === 'string' ? input['query'] : undefined) || 'No memories stored yet.';
}

export function handleMemoryStore(input: Record<string, unknown>, ctx: PluginCtx): string {
  const cfg     = resolveConfig(ctx);
  const db      = getInstance(resolveAgentId(ctx), resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
  const content = String(input['content'] ?? '');
  const topic   = typeof input['topic'] === 'string' ? input['topic'] : undefined;
  const tier    = (input['tier'] as MemoryTier | undefined) ?? 'semantic';
  const mem     = db.remember(content, { topic, tier });
  return `Stored memory #${mem.id}${topic ? ` (topic: ${topic})` : ''}.`;
}

export function handleMemoryForget(input: Record<string, unknown>, ctx: PluginCtx): string {
  const cfg   = resolveConfig(ctx);
  const db    = getInstance(resolveAgentId(ctx), resolveDbPath(cfg.dbPath), cfg.autoResolveConflicts ?? true);
  const topic = String(input['topic'] ?? '');
  db.remember('', { topic, resolveConflicts: true });
  return `Archived all memories for topic: ${topic}.`;
}

// ─── Plugin entry — framework-agnostic export ─────────────────────────────────
// OpenClaw's plugin loader calls this. The exact API shape varies by version;
// we export both a default object and named handlers so either loader style works.

export const pensieveMemoryPlugin = {
  name: 'pensieve-memory',
  tools: {
    memory_search: {
      description: 'Search agent memories using full-text search (FTS5).',
      inputSchema: {
        type: 'object',
        properties: {
          query:      { type: 'string' },
          maxResults: { type: 'number' },
          tier:       { type: 'string', enum: ['episodic', 'semantic', 'core', 'all'] },
        },
        required: ['query'],
        additionalProperties: false,
      },
      execute: handleMemorySearch,
    },
    memory_get: {
      description: 'Get full formatted memory context for the current agent.',
      inputSchema: {
        type: 'object',
        properties: { query: { type: 'string' } },
        additionalProperties: false,
      },
      execute: handleMemoryGet,
    },
    memory_store: {
      description: 'Store a new memory. Set topic for conflict resolution.',
      inputSchema: {
        type: 'object',
        properties: {
          content: { type: 'string' },
          topic:   { type: 'string' },
          tier:    { type: 'string', enum: ['episodic', 'semantic', 'core'] },
        },
        required: ['content'],
        additionalProperties: false,
      },
      execute: handleMemoryStore,
    },
    memory_forget: {
      description: 'Archive (soft-delete) memories matching a topic.',
      inputSchema: {
        type: 'object',
        properties: { topic: { type: 'string' } },
        required: ['topic'],
        additionalProperties: false,
      },
      execute: handleMemoryForget,
    },
  },
};

export default pensieveMemoryPlugin;
