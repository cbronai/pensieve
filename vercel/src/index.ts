/**
 * @pensieve/vercel — Pensieve memory tools for the Vercel AI SDK
 *
 * Exposes Pensieve's hierarchical SQLite memory as Vercel AI SDK tools
 * compatible with generateText, streamText, and the useChat hook.
 *
 * Usage — server action / route handler:
 *
 *   import { generateText } from 'ai';
 *   import { anthropic } from '@ai-sdk/anthropic';
 *   import { createPensieveTools, getPensieveSystemPrompt } from '@pensieve/vercel';
 *
 *   const tools = createPensieveTools({ userId: 'alice' });
 *
 *   const { text } = await generateText({
 *     model: anthropic('claude-opus-4-6'),
 *     system: getPensieveSystemPrompt({ userId: 'alice' }),
 *     tools,
 *     messages,
 *   });
 *
 * Usage — Next.js App Router API route (streaming):
 *
 *   import { streamText } from 'ai';
 *   import { openai } from '@ai-sdk/openai';
 *   import { createPensieveTools, getPensieveSystemPrompt } from '@pensieve/vercel';
 *
 *   export async function POST(req: Request) {
 *     const { messages, userId } = await req.json();
 *     const result = streamText({
 *       model: openai('gpt-4o'),
 *       system: getPensieveSystemPrompt({ userId }),
 *       tools: createPensieveTools({ userId }),
 *       messages,
 *     });
 *     return result.toDataStreamResponse();
 *   }
 */

import { tool } from 'ai';
import { z } from 'zod';
import { PensieveLocal, type MemoryTier } from '@pensieve/local';
import os from 'node:os';
import path from 'node:path';

// ── Instance cache (per userId per process) ───────────────────────────────────

const _cache = new Map<string, PensieveLocal>();

function getInstance(userId: string, dbPath?: string): PensieveLocal {
  const key = `${userId}:${dbPath ?? ''}`;
  if (!_cache.has(key)) {
    const resolved = dbPath
      ?? process.env['PENSIEVE_DB_PATH']
      ?? path.join(os.homedir(), '.pensieve', 'memory.db');
    _cache.set(key, new PensieveLocal({ userId, dbPath: resolved }));
  }
  return _cache.get(key)!;
}

// ── Options ───────────────────────────────────────────────────────────────────

export interface PensieveToolsOptions {
  /** Stable user/agent identifier scoping all memories */
  userId: string;
  /** SQLite database path. Defaults to PENSIEVE_DB_PATH or ~/.pensieve/memory.db */
  dbPath?: string;
}

// ── Tool factory ──────────────────────────────────────────────────────────────

/**
 * Create Vercel AI SDK tool definitions backed by Pensieve memory.
 *
 * Pass the returned object directly to `generateText` or `streamText` as `tools`.
 */
export function createPensieveTools(opts: PensieveToolsOptions) {
  const { userId, dbPath } = opts;

  return {
    memory_store: tool({
      description:
        'Store a fact in Pensieve persistent memory. ' +
        'Provide a topic for automatic conflict resolution — ' +
        'a new fact on the same topic silently archives the old one.',
      parameters: z.object({
        content: z.string().describe('The fact or information to remember'),
        topic:   z.string().optional().describe('Semantic topic for conflict resolution'),
        tier:    z.enum(['episodic', 'semantic', 'core']).optional().default('semantic')
                  .describe('Memory tier'),
      }),
      execute: async ({ content, topic, tier }) => {
        const db  = getInstance(userId, dbPath);
        const mem = db.remember(content, { topic, tier: tier as MemoryTier });
        const suffix = topic ? ` (topic: ${topic})` : '';
        return `Stored memory #${mem.id}${suffix}.`;
      },
    }),

    memory_search: tool({
      description: 'Search Pensieve persistent memory using full-text search. Returns relevant facts grouped by tier.',
      parameters: z.object({
        query:       z.string().describe('Search query'),
        max_results: z.number().int().optional().default(10).describe('Max results'),
        tier:        z.enum(['episodic', 'semantic', 'core']).optional(),
      }),
      execute: async ({ query, max_results, tier }) => {
        const db      = getInstance(userId, dbPath);
        const results = db.recall(query, { limit: max_results, tier: tier as MemoryTier });
        if (results.length === 0) return 'No relevant memories found.';
        return results.map(m => `[${m.tier}] ${m.content}`).join('\n');
      },
    }),

    memory_context: tool({
      description: 'Get the full formatted memory context (all tiers) for the current user.',
      parameters: z.object({
        query: z.string().optional().describe('Optional topic to focus context retrieval'),
      }),
      execute: async ({ query }) => {
        const db = getInstance(userId, dbPath);
        return db.getContext(query) || 'No memories stored yet.';
      },
    }),
  };
}

/**
 * Build a system prompt string that includes the current Pensieve memory context.
 *
 * @param opts.userId  - user ID to load memory for
 * @param opts.dbPath  - optional SQLite path
 * @param opts.base    - your own system prompt (memory block is appended)
 */
export function getPensieveSystemPrompt(opts: PensieveToolsOptions & { base?: string }): string {
  const db = getInstance(opts.userId, opts.dbPath);
  const context = db.getContext();

  const memoryBlock =
    'You have access to Pensieve — a persistent hierarchical memory.\n' +
    '- Use `memory_store` to save important facts (with a topic for conflict resolution).\n' +
    '- Use `memory_search` to recall relevant context before answering.\n' +
    (context ? '\n' + context + '\n' : '');

  return opts.base ? `${opts.base}\n\n${memoryBlock}` : memoryBlock;
}

/**
 * Sync a completed turn to episodic memory.
 * Call after each exchange to persist the conversation.
 */
export function syncTurn(userId: string, userMessage: string, assistantMessage: string, dbPath?: string): void {
  const db = getInstance(userId, dbPath);
  db.remember(`User: ${userMessage}`, { tier: 'episodic' });
  db.remember(`Assistant: ${assistantMessage}`, { tier: 'episodic' });
}
