/**
 * Pensieve Memory — Paperclip Plugin Manifest
 *
 * Gives every agent in a Paperclip company persistent hierarchical memory.
 * Agents gain memory_store and memory_search tools automatically.
 *
 * Install:
 *   paperclipai plugin install @pensieve/paperclip
 *
 * The plugin:
 *  - Injects memory_store / memory_search tools into every agent's tool set
 *  - Listens to agent turn events to auto-sync episodic memory
 *  - Provides a UI panel for viewing and editing memories (GDPR compliance)
 */

import type { PluginManifest } from '@paperclipai/plugin-sdk';

export const manifest = {
  id:          'pensieve-memory',
  name:        'Pensieve Memory',
  description: 'Hierarchical persistent memory for every agent — SQLite-backed, local-first, conflict-resolving.',
  version:     '0.1.0',

  capabilities: [
    'tools',
    'events',
    'state',
    'database.namespace.migrate',
    'database.namespace.read',
    'database.namespace.write',
    'api.routes.register',
  ],

  database: {
    migrationsDir: 'migrations',
    coreReadTables: ['agents', 'issues'],
  },

  tools: [
    {
      name:        'memory_store',
      description: 'Store a fact in Pensieve persistent memory. Provide a topic for automatic conflict resolution — new fact archives the old one on the same topic.',
      inputSchema: {
        type: 'object',
        properties: {
          content: { type: 'string', description: 'The fact or context to remember' },
          topic:   { type: 'string', description: 'Semantic topic (enables conflict resolution)' },
          tier:    { type: 'string', enum: ['episodic', 'semantic', 'core'], description: 'Memory tier' },
        },
        required: ['content'],
      },
    },
    {
      name:        'memory_search',
      description: 'Search Pensieve persistent memory using full-text search. Returns relevant facts grouped by tier.',
      inputSchema: {
        type: 'object',
        properties: {
          query:       { type: 'string', description: 'Search query' },
          max_results: { type: 'integer', description: 'Max results (default 10)' },
          tier:        { type: 'string', enum: ['episodic', 'semantic', 'core'] },
        },
        required: ['query'],
      },
    },
  ],

  apiRoutes: [
    {
      routeKey:          'list-memories',
      method:            'GET' as const,
      path:              '/agents/:agentId/memories',
      auth:              'board-or-agent' as const,
      capability:        'api.routes.register',
      companyResolution: { from: 'agent' as const, param: 'agentId' },
    },
    {
      routeKey:          'archive-memory',
      method:            'DELETE' as const,
      path:              '/memories/:memoryId',
      auth:              'board-or-agent' as const,
      capability:        'api.routes.register',
      companyResolution: { from: 'session' as const },
    },
  ],
} satisfies PluginManifest;
