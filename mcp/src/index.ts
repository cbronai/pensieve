#!/usr/bin/env node
/**
 * @pensieve/mcp — Pensieve Memory MCP Server
 *
 * Exposes Pensieve's hierarchical SQLite memory as MCP tools.
 * Works with any MCP-compatible client: Claude Desktop, Cursor, Windsurf, etc.
 *
 * Usage (stdio transport — default):
 *   npx @pensieve/mcp --user-id alice
 *
 * Claude Desktop config (~/.claude/claude_desktop_config.json):
 *   {
 *     "mcpServers": {
 *       "pensieve": {
 *         "command": "npx",
 *         "args": ["@pensieve/mcp", "--user-id", "alice"],
 *         "env": { "PENSIEVE_DB_PATH": "~/.pensieve/memory.db" }
 *       }
 *     }
 *   }
 *
 * Tools exposed:
 *   memory_store   — store a fact (with optional topic for conflict resolution)
 *   memory_search  — full-text search across memory tiers
 *   memory_context — get formatted context string for system prompt injection
 *   memory_forget  — archive all memories on a topic
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import { PensieveLocal, type MemoryTier } from '@pensieve/local';
import os from 'node:os';
import path from 'node:path';

// ── Config ────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const userIdIdx = args.indexOf('--user-id');
const userId = userIdIdx !== -1 ? args[userIdIdx + 1] : (process.env['PENSIEVE_USER_ID'] ?? 'default');

const dbPath = process.env['PENSIEVE_DB_PATH']
  ?? path.join(os.homedir(), '.pensieve', 'memory.db');

const pensieve = new PensieveLocal({ userId, dbPath });

// ── Tool definitions ──────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: 'memory_store',
    description:
      'Store a fact in Pensieve persistent memory. ' +
      'Provide a topic for automatic conflict resolution — ' +
      'a new fact on the same topic silently archives the old one.',
    inputSchema: {
      type: 'object',
      properties: {
        content: { type: 'string', description: 'The fact or information to remember' },
        topic:   { type: 'string', description: 'Semantic topic for conflict resolution' },
        tier:    { type: 'string', enum: ['episodic', 'semantic', 'core'],
                   description: 'Memory tier (default: semantic)' },
      },
      required: ['content'],
      additionalProperties: false,
    },
  },
  {
    name: 'memory_search',
    description: 'Search Pensieve persistent memory using full-text search. Returns relevant facts grouped by tier.',
    inputSchema: {
      type: 'object',
      properties: {
        query:       { type: 'string',  description: 'Search query' },
        max_results: { type: 'integer', description: 'Max results (default 10)' },
        tier:        { type: 'string',  enum: ['episodic', 'semantic', 'core'] },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'memory_context',
    description: 'Get the full formatted memory context string (all tiers) for injecting into a system prompt.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Optional topic to focus context retrieval' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'memory_forget',
    description: 'Archive (soft-delete) all active memories with the given topic.',
    inputSchema: {
      type: 'object',
      properties: {
        topic: { type: 'string', description: 'Exact topic to archive' },
      },
      required: ['topic'],
      additionalProperties: false,
    },
  },
] as const;

// ── Server ────────────────────────────────────────────────────────────────────

const server = new Server(
  { name: 'pensieve-memory', version: '0.1.0' },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: input = {} } = request.params;

  try {
    if (name === 'memory_store') {
      const { content, topic, tier } = input as {
        content: string; topic?: string; tier?: MemoryTier;
      };
      const mem = pensieve.remember(content, { topic, tier: tier ?? 'semantic' });
      const suffix = topic ? ` (topic: ${topic})` : '';
      return { content: [{ type: 'text', text: `Stored memory #${mem.id}${suffix}.` }] };
    }

    if (name === 'memory_search') {
      const { query, max_results, tier } = input as {
        query: string; max_results?: number; tier?: MemoryTier;
      };
      const results = pensieve.recall(query, { limit: max_results ?? 10, tier });
      if (results.length === 0) {
        return { content: [{ type: 'text', text: 'No relevant memories found.' }] };
      }
      const text = results.map(m => `[${m.tier}] ${m.content}`).join('\n');
      return { content: [{ type: 'text', text }] };
    }

    if (name === 'memory_context') {
      const { query } = input as { query?: string };
      const context = pensieve.getContext(query);
      return { content: [{ type: 'text', text: context || 'No memories stored yet.' }] };
    }

    if (name === 'memory_forget') {
      const { topic } = input as { topic: string };
      // Archive by storing empty content on same topic with conflict resolution
      pensieve.remember('', { topic, resolveConflicts: true });
      return { content: [{ type: 'text', text: `Archived all memories for topic: ${topic}.` }] };
    }

    return { content: [{ type: 'text', text: `Unknown tool: ${name}` }], isError: true };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { content: [{ type: 'text', text: `Error: ${msg}` }], isError: true };
  }
});

// ── Start ─────────────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write(`Pensieve MCP server started (user=${userId} db=${dbPath})\n`);
}

main().catch((err) => {
  process.stderr.write(`Fatal: ${err}\n`);
  process.exit(1);
});
