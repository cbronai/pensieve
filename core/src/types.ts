export type MemoryTier = 'episodic' | 'semantic' | 'core';
export type MemoryStatus = 'active' | 'archived';

export interface Memory {
  id: number;
  userId: string;
  content: string;
  topic?: string;
  tier: MemoryTier;
  status: MemoryStatus;
  createdAt: Date;
  updatedAt: Date;
}

export interface RememberOptions {
  /** Semantic topic for this memory — used for conflict resolution */
  topic?: string;
  /** Memory tier: episodic (raw), semantic (facts), core (identity) */
  tier?: MemoryTier;
  /** Override auto-conflict resolution for this specific call */
  resolveConflicts?: boolean;
}

export interface RecallOptions {
  limit?: number;
  tier?: MemoryTier;
  includeArchived?: boolean;
}

export interface ChatContext {
  message: string;
  /** Formatted memory context to inject into system prompt */
  memories: string;
  systemPrompt?: string;
}

export interface ChatOptions {
  message: string;
  llmCall: (context: ChatContext) => Promise<string>;
  systemPrompt?: string;
}

export interface PensieveLocalOptions {
  userId: string;
  /** SQLite db path. Defaults to ~/.pensieve/memory.db */
  dbPath?: string;
  /** Automatically archive conflicting memories on same topic. Default: true */
  autoResolveConflicts?: boolean;
}
