import Database from 'better-sqlite3';
import path from 'path';
import os from 'os';
import fs from 'fs';

export interface RawMemory {
  id: number;
  user_id: string;
  content: string;
  topic: string | null;
  tier: string;
  status: string;
  created_at: number;
  updated_at: number;
}

export class SQLiteStore {
  private db: Database.Database;

  constructor(dbPath?: string) {
    const resolved = dbPath
      ? path.resolve(dbPath.replace(/^~/, os.homedir()))
      : path.join(os.homedir(), '.pensieve', 'memory.db');

    const dir = path.dirname(resolved);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    this.db = new Database(resolved);
    this.initialize();
  }

  private initialize(): void {
    this.db.exec(`
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
        content,
        topic,
        content=memories,
        content_rowid=id
      );

      CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, topic)
        VALUES (new.id, new.content, new.topic);
      END;

      CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, topic)
        VALUES ('delete', old.id, old.content, old.topic);
      END;

      CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, topic)
        VALUES ('delete', old.id, old.content, old.topic);
        INSERT INTO memories_fts(rowid, content, topic)
        VALUES (new.id, new.content, new.topic);
      END;
    `);
  }

  insert(userId: string, content: string, topic: string | null, tier: string): RawMemory {
    const now = Date.now();
    const result = this.db.prepare(`
      INSERT INTO memories (user_id, content, topic, tier, status, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'active', ?, ?)
    `).run(userId, content, topic, tier, now, now);

    return this.getById(result.lastInsertRowid as number)!;
  }

  getById(id: number): RawMemory | null {
    return this.db.prepare('SELECT * FROM memories WHERE id = ?').get(id) as RawMemory | null;
  }

  search(userId: string, query: string, limit = 10, tier?: string, includeArchived = false): RawMemory[] {
    const statusClause = includeArchived ? '' : "AND m.status = 'active'";
    const tierClause   = tier ? `AND m.tier = '${tier}'` : '';

    return this.db.prepare(`
      SELECT m.* FROM memories m
      JOIN memories_fts ON memories_fts.rowid = m.id
      WHERE memories_fts MATCH ?
        AND m.user_id = ?
        ${statusClause}
        ${tierClause}
      ORDER BY rank
      LIMIT ?
    `).all(query, userId, limit) as RawMemory[];
  }

  getRecent(userId: string, limit = 20, tier?: string): RawMemory[] {
    const tierClause = tier ? `AND tier = ?` : '';
    const params: (string | number)[] = [userId];
    if (tier) params.push(tier);
    params.push(limit);

    return this.db.prepare(`
      SELECT * FROM memories
      WHERE user_id = ? AND status = 'active' ${tierClause}
      ORDER BY created_at DESC
      LIMIT ?
    `).all(...params) as RawMemory[];
  }

  archiveByTopic(userId: string, topic: string): number {
    const now = Date.now();
    const result = this.db.prepare(`
      UPDATE memories
      SET status = 'archived', updated_at = ?
      WHERE user_id = ? AND topic = ? AND status = 'active'
    `).run(now, userId, topic);
    return result.changes;
  }

  close(): void {
    this.db.close();
  }
}
