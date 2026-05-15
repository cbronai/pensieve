import { SQLiteStore, RawMemory } from '../storage/SQLiteStore';
import { Memory, MemoryTier, RememberOptions, RecallOptions } from '../types';

function toMemory(raw: RawMemory): Memory {
  return {
    id: raw.id,
    userId: raw.user_id,
    content: raw.content,
    topic: raw.topic ?? undefined,
    tier: raw.tier as MemoryTier,
    status: raw.status as 'active' | 'archived',
    createdAt: new Date(raw.created_at),
    updatedAt: new Date(raw.updated_at),
  };
}

export class MemoryManager {
  constructor(
    private store: SQLiteStore,
    private userId: string,
    private autoResolveConflicts: boolean,
  ) {}

  remember(content: string, options: RememberOptions = {}): Memory {
    const { topic, tier = 'episodic', resolveConflicts = this.autoResolveConflicts } = options;

    // Archive stale memories on the same topic before inserting
    if (resolveConflicts && topic) {
      this.store.archiveByTopic(this.userId, topic);
    }

    const raw = this.store.insert(this.userId, content, topic ?? null, tier);
    return toMemory(raw);
  }

  recall(query: string, options: RecallOptions = {}): Memory[] {
    const { limit = 10, tier, includeArchived = false } = options;

    let rows: RawMemory[] = [];
    try {
      rows = this.store.search(this.userId, query, limit, tier, includeArchived);
    } catch {
      // FTS query can fail on special chars — fall back to recency
      rows = this.store.getRecent(this.userId, limit, tier);
    }

    if (rows.length === 0) {
      rows = this.store.getRecent(this.userId, limit, tier);
    }

    return rows.map(toMemory);
  }

  getContext(query?: string): string {
    const core = this.store.getRecent(this.userId, 5, 'core');

    const semantic = query
      ? (() => {
          try {
            return this.store.search(this.userId, query, 5, 'semantic');
          } catch {
            return this.store.getRecent(this.userId, 5, 'semantic');
          }
        })()
      : this.store.getRecent(this.userId, 5, 'semantic');

    const episodic = this.store.getRecent(this.userId, 5, 'episodic');

    const sections: string[] = [];
    if (core.length > 0)     sections.push('## User Profile\n'     + core.map(m => `- ${m.content}`).join('\n'));
    if (semantic.length > 0) sections.push('## Key Facts\n'        + semantic.map(m => `- ${m.content}`).join('\n'));
    if (episodic.length > 0) sections.push('## Recent Context\n'   + episodic.map(m => `- ${m.content}`).join('\n'));

    return sections.join('\n\n');
  }
}
