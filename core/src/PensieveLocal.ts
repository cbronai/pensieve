import { SQLiteStore } from './storage/SQLiteStore';
import { MemoryManager } from './memory/MemoryManager';
import { Memory, RememberOptions, RecallOptions, ChatOptions, PensieveLocalOptions } from './types';

export class PensieveLocal {
  private store: SQLiteStore;
  private manager: MemoryManager;

  constructor(options: PensieveLocalOptions) {
    const { userId, dbPath, autoResolveConflicts = true } = options;
    this.store   = new SQLiteStore(dbPath);
    this.manager = new MemoryManager(this.store, userId, autoResolveConflicts);
  }

  /**
   * Store a memory. If `topic` is set and a memory with the same topic exists,
   * the old one is automatically archived (conflict resolution).
   */
  remember(content: string, options?: RememberOptions): Memory {
    return this.manager.remember(content, options);
  }

  /**
   * Retrieve memories relevant to a query via SQLite FTS5.
   * Falls back to most-recent if query yields no results.
   */
  recall(query: string, options?: RecallOptions): Memory[] {
    return this.manager.recall(query, options);
  }

  /**
   * Build a formatted context string (markdown sections) to inject into an LLM system prompt.
   */
  getContext(query?: string): string {
    return this.manager.getContext(query);
  }

  /**
   * Convenience wrapper: stores the message, injects memory context, calls your LLM,
   * then stores the response — all in one call.
   */
  async chat(options: ChatOptions): Promise<string> {
    const { message, llmCall, systemPrompt } = options;

    this.manager.remember(message, { tier: 'episodic' });

    const memories = this.manager.getContext(message);
    const response = await llmCall({ message, memories, systemPrompt });

    this.manager.remember(`Assistant: ${response}`, { tier: 'episodic' });

    return response;
  }

  close(): void {
    this.store.close();
  }
}
