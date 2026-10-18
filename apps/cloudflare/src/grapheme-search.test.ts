import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { suggest } from './index';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
// The part of D1 `suggest` uses, over SQLite.
const d1 = (db: Database) => ({
  prepare: (sql: string) => {
    const bound = (values: unknown[]) => ({
      all: async () => ({ results: db.query(sql).all(...(values as never[])) }),
      first: async () => db.query(sql).get(...(values as never[])),
    });
    return { ...bound([]), bind: (...values: unknown[]) => bound(values) };
  },
});
const code = (c: string) => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0');

describe('a search lists each grapheme it names with all its forms', () => {
  const db = migrated();
  const ne = ['ね', 'ネ', '𛂒', '𛄧'];
  const grapheme = { code_point: code('ね'), char: 'ね', label: ne.join(' = '), character_count: ne.length,
    members: ne.map(c => ({ code_point: code(c), char: c })) };
  const card = (c: string, count: number, family = c === 'ぬ' ? null : grapheme) => JSON.stringify({ char: c, code_point: code(c),
    occurrence_count: count, grapheme: family ?? { code_point: code(c), char: c, label: c, character_count: 1, members: [{ code_point: code(c), char: c }] } });
  const character = db.prepare('INSERT INTO characters(code_point,character,name,data,detail) VALUES(?,?,?,?,?)');
  for (const [c, n] of [['ね', 50], ['ネ', 40], ['𛂒', 30], ['𛄧', 20], ['ぬ', 5]] as const) character.run(code(c), c, c, card(c, n), '{}');
  const alias = db.prepare('INSERT INTO aliases(query,code_point,rank) VALUES(?,?,?)');
  alias.run('𛄧', code('𛄧'), 0);
  alias.run('ね', code('ね'), 0);
  alias.run('ね', code('𛂒'), 4);
  alias.run('ね', code('ぬ'), 4);
  const env = { DB: d1(db) } as unknown as Env;
  const chars = async (q: string) => (await suggest(env, new URLSearchParams({ q, limit: '8' }))).items.map((item: { char: string }) => item.char);

  it('puts the forms the query found first, then the rest of the grapheme', async () => {
    expect(await chars('𛄧')).toEqual(['𛄧', 'ね', 'ネ', '𛂒']);
  });
  it('keeps a grapheme together where the ranking would split it', async () => {
    expect(await chars('ね')).toEqual(['ね', '𛂒', 'ネ', '𛄧', 'ぬ']);
  });
});
