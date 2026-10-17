import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { writersQuery, writesQuery } from './index';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}

describe('the graphemes a reduced form writes', () => {
  it('reads what a form writes by its key, and the forms that write a grapheme by the index', () => {
    const db = migrated();
    const row = db.prepare('INSERT INTO grapheme_writers(writer,grapheme) VALUES(?,?)');
    for (const [writer, grapheme] of [['后', '後'], ['弁', '辨'], ['弁', '辯'], ['弁', '瓣']]) row.run(writer, grapheme);
    const read = (sql: string, value: string) => (db.query(sql).all(value) as { char: string }[]).map(r => r.char);
    expect(read(writesQuery(), '弁')).toEqual(['瓣', '辨', '辯']);
    expect(read(writersQuery(), '後')).toEqual(['后']);
    expect(read(writesQuery(), '時')).toEqual([]);
    expect(String(db.query(`EXPLAIN QUERY PLAN ${writersQuery()}`).all('後').map((r: any) => r.detail))).toContain('grapheme_writer_grapheme');
    db.close();
  });
});
