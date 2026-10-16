import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { pageCropsQuery, pageView } from './index';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
const record = { id: 'hl:b:3', document: 'hl:b', title: 'Book', page_number: 4, width: 100, height: 200,
  service: 'https://x.org/iiif/2/p', canvas: null, holder: 'Holder', licence: 'pd', attribution: 'Holder',
  rights_url: null, previous: 'hl:b:2', next: null, crops: ['c', 'a', 'gone', 'r'] };

describe('a source page', () => {
  it('holds its crops in the record\'s order with their labels and boxes as the site has them now', () => {
    const db = migrated();
    const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,?,'handwritten','han','pending',0,1,1,0,?,'{}','{}','{}')`);
    for (const [id, origin, label] of [['a', 'local', 'あ'], ['c', 'local', 'か'], ['r', 'retired', 'ら']])
      unit.run(id, origin, label, JSON.stringify({ id, box: { x: 1, y: 2, w: 3, h: 4 }, image: `/img/${id}.webp`, line_id: 'l', state: 'pending' }));
    const rows = db.query(pageCropsQuery()).all(JSON.stringify(record.crops)) as any;
    const view = pageView(record, rows);
    expect(view.units.map((u: any) => [u.id, u.character])).toEqual([['c', 'か'], ['a', 'あ']]);
    expect(view.units[0].box).toEqual({ x: 1, y: 2, w: 3, h: 4 });
    expect(view).toMatchObject({ id: 'hl:b:3', document_id: 'hl:b', document_title: 'Book', seq: 3, previous: 'hl:b:2', next: null, drawable: false,
      image_url: 'https://x.org/iiif/2/p/full/!3000,3000/0/default.jpg' });
    expect(view.crops).toBeUndefined();
    db.close();
  });
  it('has no photo when no IIIF service serves its image', () => {
    expect(pageView({ ...record, service: null }, []).image_url).toBeNull();
  });
  it('reads each crop by its primary key', () => {
    const db = migrated();
    const plan = (db.query('EXPLAIN QUERY PLAN ' + pageCropsQuery()).all('[]') as any[]).map(r => r.detail).join('\n');
    expect(plan).toContain('SEARCH u USING INDEX sqlite_autoindex_units_1 (id=?)');
    expect(plan).not.toMatch(/SCAN u\b/);
    db.close();
  });
});
