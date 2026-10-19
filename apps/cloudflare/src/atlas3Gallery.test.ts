import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from './forms.test';
import { bundleKey, cropId, publicId } from './atlas3';
import { SHARDS, characterCounts, characterGallery, currentPointer, galleryQuery } from './atlas3Gallery';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function bucket(objects: Map<string, Uint8Array>) {
  return { get: async (key: string) => objects.has(key) ? { body: new Response(objects.get(key)).body! } : null };
}

let pages = 5000;
// Ten crops of 所 on one page: styles 0 and 2, every third one withheld, the last two without a year.
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  db.exec(readFileSync(new URL('../migrations-atlas/0001_init.sql', import.meta.url), 'utf8'));
  const page = ++pages, ids = Array.from({ length: 10 }, (_, slot) => cropId(page, slot));
  db.exec(`INSERT INTO books VALUES(1,'hk:book'); INSERT INTO graphemes(id,text) VALUES(7,'所');
    INSERT INTO codes VALUES('state',0,'pending'),('state',1,'checked'),('style',0,'cursive'),('style',2,'unassessed');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'hk:book:1',1,1,10);`);
  ids.forEach((id, i) => db.query(`INSERT INTO crops(id,kind,book,char,style,state,quiz,year,shuffle) VALUES(?,0,1,7,?,?,?,?,?)`)
    .run(id, i < 5 ? 0 : 2, i % 2, i % 3 === 0 ? 0 : 1, i < 8 ? 1700 + i * 10 : null, 1000 - i * 7));
  const answers = { pub: 'P1', cell: ['kind', 'style', 'state', 'quiz', 'decade', 'visual_group', 'n'],
    chars: { 7: [[0, 0, 0, 1, 1700, null, 3], [0, 2, 1, 1, 1750, 4, 2]] } };
  const objects = new Map<string, Uint8Array>([
    ['v3/pointer.json', gzipSync(JSON.stringify({ pub: 'P1', through_gen: 3 }))],
    [`v3/pub/P1/counts/${7 % SHARDS}.json.gz`, gzipSync(JSON.stringify(answers))],
    [`v3/pub/P1/counts/${9 % SHARDS}.json.gz`, gzipSync(JSON.stringify({ ...answers, chars: {} }))],
    [bundleKey(page, 1), gzipSync(JSON.stringify({ key: 'hk:book:1', book: 1, rev: 1, page: null,
      crops: ids.map((_, slot) => ({ slot, id: `hk:${slot}`, label: '所', image: `/atlas/media/${slot}.webp` })) }))]]);
  const env = { ATLAS: d1(db), MEDIA: bucket(objects) } as unknown as Env;
  return { db, env, ids, objects };
}

describe('new-layout character galleries', () => {
  it('page through one index range by style, shuffle and id, leaving withheld crops out', async () => {
    const { env, ids } = setup();
    const seen: string[] = [];
    let after: string | null = null;
    do {
      const page = await characterGallery(env, { char: 7, limit: 3, after });
      seen.push(...page.items.map(item => item.source_id));
      after = page.next;
    } while (after);
    // Shown: slots 1, 2, 4 (cursive) then 5, 7, 8 (unassessed), each style by shuffle, which falls with the slot.
    expect(seen).toEqual(['hk:4', 'hk:2', 'hk:1', 'hk:8', 'hk:7', 'hk:5']);
    expect((await characterGallery(env, { char: 7, limit: 20, withheld: true })).items).toHaveLength(ids.length);
  });

  it('filter by style and state, and order by witness year', async () => {
    const { env } = setup();
    expect((await characterGallery(env, { char: 7, limit: 20, style: 2, state: 1 })).items.map(i => i.source_id)).toEqual(['hk:7', 'hk:5']);
    expect((await characterGallery(env, { char: 7, limit: 20, order: 'year' })).items.map(i => i.year)).toEqual([1710, 1720, 1740, 1750, 1770]);
  });

  it('refuse a cursor it did not write', async () => {
    const { env, ids } = setup();
    expect(() => galleryQuery({ char: 7, limit: 3, after: 'x' })).toThrow('Invalid cursor.');
    expect(galleryQuery({ char: 7, limit: 3, after: `0.993.${publicId(ids[1])}` }).values).toEqual([7, 0, 993, ids[1], 4]);
    expect((await characterGallery(env, { char: 7, limit: 3, after: `0.993.${publicId(ids[1])}` })).items.map(i => i.source_id)).toEqual(['hk:8', 'hk:7', 'hk:5']);
  });

  it('read every gallery query from a covering index', () => {
    const { db } = setup();
    for (const q of [{ char: 7, limit: 3 }, { char: 7, limit: 3, style: 2, state: 1, after: `2.1.${publicId(cropId(1, 1))}` }, { char: 7, limit: 3, order: 'year' as const }]) {
      const { sql, values } = galleryQuery(q);
      const plan = (db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...values) as { detail: string }[]).map(r => r.detail).join(' | ');
      expect(plan).toMatch(/COVERING INDEX crop_(char|year)/);
      expect(plan).not.toContain('TEMP B-TREE');
    }
  });
});

describe('new-layout counts', () => {
  it('add the reviews since the publication to its answer', async () => {
    const { db, env } = setup();
    db.exec(`INSERT INTO tally VALUES(3,0,7,1,-1,0,0,1,5),(4,0,7,1,-1,0,0,1,-1),(4,0,7,1,-1,0,1,1,1)`);
    const cells = (await characterCounts(env, [7, 9])).get(7)!;
    expect(cells.filter(c => !c.fresh).map(c => c.n)).toEqual([3, 2]);
    // Only generation 4 counts: generation 3 is in the answer already.
    expect(cells.filter(c => c.fresh).map(c => [c.state, c.n])).toEqual([[0, -1], [1, 1]]);
    expect(cells.reduce((sum, c) => sum + c.n, 0)).toBe(5);
    expect((await characterCounts(env, [9])).get(9)).toEqual([]);
  });

  it('read the pointer again after a minute', async () => {
    const { env, objects } = setup();
    expect((await currentPointer(env, 4e12)).pub).toBe('P1');
    objects.set('v3/pointer.json', gzipSync(JSON.stringify({ pub: 'P2', through_gen: 4 })));
    expect((await currentPointer(env, 4e12 + 30_000)).pub).toBe('P1');
    expect((await currentPointer(env, 4e12 + 61_000)).pub).toBe('P2');
  });
});
