import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { searchVariantsQuery } from './index';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
const code = (c: string) => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0');

describe("a one-character search's variants", () => {
  it('lists both directions of the graph, the most attested first, held characters only, and only pairs the card calls variants', () => {
    const db = migrated();
    const character = db.prepare('INSERT INTO characters(code_point,character,name,data,detail) VALUES(?,?,?,?,?)');
    for (const c of '鯡𩹉鮓鮺蚱鮨') character.run(code(c), c, c, JSON.stringify({ char: c }), '{}');
    const edge = db.prepare("INSERT INTO character_variants(a,b,relation,source,detail,written,widens) VALUES(?,?,?,?,'',1,?)");
    edge.run('鯡', '𩹉', 'variant', 'cjkvi-variants', 1);
    edge.run('鮓', '鮺', 'variant', 'cjkvi-variants', 1);
    edge.run('鮺', '鮓', 'variant', 'wikidata', 1);
    edge.run('蚱', '鮓', 'borrowed', 'cjkvi-variants', 0);
    edge.run('鮓', '𩼮', 'variant', 'wikidata', 1);   // not in the character table
    edge.run('鮓', '鮨', 'variant', 'cjkvi-variants', 1);
    edge.run('鮓', '鮨', 'simplified', 'unihan', 0);   // a simplified pair is kept apart
    const read = (c: string, limit = 9) => (db.query(searchVariantsQuery()).all(c, c, c, limit) as { data: string }[]).map(r => JSON.parse(r.data).char);
    expect(read('鯡')).toEqual(['𩹉']);
    expect(read('𩹉')).toEqual(['鯡']);
    expect(read('鮓')).toEqual(['鮺']);
    expect(read('蚱')).toEqual([]);
    db.close();
  });
});
