import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from './forms.test';
import { bundleKey, cropId, publicId } from './atlas3';
import { SHARDS, characterCounts, currentPointer, cursorOf, familyClaims, filedCell, filedClaimsQuery, filedCounts, galleryPage, rangeQuery, tallyQuery,
  type GalleryQuery } from './atlas3Gallery';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function bucket(objects: Map<string, Uint8Array>) {
  return { get: async (key: string) => objects.has(key) ? { body: new Response(objects.get(key)).body! } : null };
}

let pages = 5000;
// Ten crops of 所 on one page: styles 0 and 2, every third one withheld, the last two without a year.
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL('../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  const page = ++pages, ids = Array.from({ length: 10 }, (_, slot) => cropId(page, slot));
  db.exec(`INSERT INTO books(id,key) VALUES(1,'hk:book'); INSERT INTO graphemes(id,text) VALUES(7,'所');
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
  const page = (env: Env, q: Partial<GalleryQuery>) => galleryPage(env, { kind: 0, segments: [[{ by: 'char', key: 7 }]], limit: 20, ...q });
  const slots = (found: { ids: number[] }) => found.ids.map(id => id % 2 ** 24);
  it('page through one index range by style, shuffle and id, leaving withheld crops out', async () => {
    const { env, ids } = setup();
    const seen: number[] = [];
    let after: string | null = null;
    do {
      const found = await page(env, { limit: 3, after });
      seen.push(...slots(found));
      after = found.next;
    } while (after);
    // Shown: slots 1, 2, 4 (cursive) then 5, 7, 8 (unassessed), each style by shuffle, which falls with the slot.
    expect(seen).toEqual([4, 2, 1, 8, 7, 5]);
    expect((await page(env, { withheld: true })).ids).toHaveLength(ids.length);
  });

  it('filter by style group and state, and order by witness year with the undated last', async () => {
    const { env } = setup();
    expect(slots(await page(env, { group: 'unassessed', state: 1 }))).toEqual([7, 5]);
    expect((await page(env, { group: 'formal' })).ids).toEqual([]);
    expect(slots(await page(env, { order: 'year' }))).toEqual([1, 2, 4, 5, 7, 8]);
    expect(slots(await page(env, { order: 'year', years: { from: 1720, to: 1750 } }))).toEqual([2, 4, 5]);
    // A year range in style order is sorted from the range of `crop_year`.
    expect(slots(await page(env, { years: { from: 1720, to: 1750 } }))).toEqual([4, 2, 5]);
    expect(slots(await page(env, { years: 'undated', withheld: true }))).toEqual([9, 8]);
    // Page by page across the dated and undated ranges.
    const seen: number[] = [];
    let after: string | null = null;
    do {
      const found = await page(env, { limit: 2, order: 'year', withheld: true, after });
      seen.push(...slots(found));
      after = found.next;
    } while (after);
    expect(seen).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 9, 8]);
  });

  it('merge the ranges of several sources, and list segments one after another', async () => {
    const { env } = setup();
    // Two segments of the same character list it twice, each in full before the next.
    const twice = await page(env, { segments: [[{ by: 'char', key: 7 }], [{ by: 'char', key: 7 }]], limit: 4 });
    expect(slots(twice)).toEqual([4, 2, 1, 8]);
    expect(slots(await page(env, { segments: [[{ by: 'char', key: 7 }], [{ by: 'char', key: 7 }]], limit: 4, after: twice.next }))).toEqual([7, 5, 4, 2]);
    expect(await page(env, { segments: [] })).toEqual({ ids: [], next: null });
  });

  it('refuse a cursor it did not write', async () => {
    const { env, ids } = setup();
    expect(() => cursorOf('x')).toThrow('Invalid cursor.');
    expect(() => cursorOf(`0Snull.98.${publicId(ids[1])}`)).toThrow('Invalid cursor.');
    expect(() => rangeQuery({ kind: 0, segments: [], limit: 1 }, 'S', { by: 'char', key: 7 }, null, 0)).toThrow('Invalid limit.');
    await expect(page(env, { after: `3S0.993.${publicId(ids[1])}` })).rejects.toThrow('Invalid cursor.');
    expect(slots(await page(env, { limit: 3, after: `0S0.993.${publicId(ids[1])}` }))).toEqual([8, 7, 5]);
  });

  it('read every gallery query from a covering index', () => {
    const { db } = setup();
    const after: [number, number, number] = [2, 1, cropId(1, 1)];
    for (const [q, phase, index] of [[{}, 'S', 'crop_char'], [{ group: 'unassessed', state: 1 }, 'S', 'crop_char'], [{ group: 'formal' }, 'S', 'crop_char'],
      [{ years: { from: 1700, to: 1800 } }, 'Y', 'crop_year'], [{ withheld: true }, 'U', 'crop_undated']] as const) {
      for (const from of [null, after]) {
        const { sql, values } = rangeQuery({ kind: 0, segments: [], limit: 3, ...q } as GalleryQuery, phase, { by: 'char', key: 7 }, from, 4)!;
        const plan = (db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...values) as { detail: string }[]).map(r => r.detail).join(' | ');
        expect(plan).toContain(`COVERING INDEX ${index}`);
        expect(plan).not.toContain('TEMP B-TREE');
      }
    }
    const tally = (db.query(`EXPLAIN QUERY PLAN ${tallyQuery()}`).all(3, '[7]') as { detail: string }[]).map(r => r.detail).join(' | ');
    expect(tally).toContain('USING PRIMARY KEY (gen=? AND char=?)');
    const filed = (db.query(`EXPLAIN QUERY PLAN ${tallyQuery('family')}`).all(3, '[9]') as { detail: string }[]).map(r => r.detail).join(' | ');
    expect(filed).toContain('USING PRIMARY KEY (gen=? AND char=? AND family=?)');
    // A family that leaves out its crops filed elsewhere still reads its index alone.
    const { sql, values } = rangeQuery({ kind: 0, segments: [], limit: 3 }, 'S', { by: 'char', key: 7, except: [cropId(1, 1)] }, after, 4)!;
    expect((db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...values) as { detail: string }[]).map(r => r.detail).join(' | ')).toContain('COVERING INDEX crop_char');
  });
});

describe('crops a grapheme claim files', () => {
  // Slot 1 of 所 read as 後 (on its cut), slot 2 read as 後 on a cut since replaced, slot 4 read as 所.
  function claimed() {
    const found = setup(), { db, ids } = found;
    db.exec(`INSERT INTO graphemes(id,text) VALUES(8,'後'); UPDATE crops SET cut=1 WHERE id=${ids[2]};`);
    const claim = db.prepare(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,value,members,supporting,claims,resolver,at)
      VALUES(?,'has_grapheme','','',?,0,?,?,NULL,'[]','[]','[]','r','t')`);
    claim.run('hk:1', ids[1], 'asserted', 'U+5F8C'); claim.run('hk:2', ids[2], 'accepted', 'U+5F8C'); claim.run('hk:4', ids[4], 'asserted', 'U+6240');
    claim.run('hk:5', ids[5], 'disputed', 'U+5F8C');
    return found;
  }
  const slotsOf = (ids: number[]) => ids.map(id => id % 2 ** 24);
  it('leave the family of their character for the one their claim names, on the cut it holds on', async () => {
    const { env, ids } = claimed();
    const so = await familyClaims(env, 'U+6240', [7]), after = await familyClaims(env, 'U+5F8C', [8]);
    expect(slotsOf(so.away.map(r => r.id))).toEqual([1]);
    expect(so.here).toEqual([]);
    expect(slotsOf(after.here.map(r => r.id))).toEqual([1]);
    expect(filedCell(after.here[0], 1)).toEqual({ kind: 0, style: 0, state: 1, quiz: 1, decade: 1710, visual_group: null, n: 1 });
    const family = (g: { except?: number[]; ids?: number[] }) => galleryPage(env, { kind: 0, limit: 20, withheld: true,
      segments: [[{ by: 'char', key: 7, except: g.except }, ...(g.ids ? [{ by: 'claimed' as const, key: 8, ids: g.ids }] : [])]] });
    expect(slotsOf((await family({ except: so.away.map(r => r.id) })).ids)).not.toContain(1);
    expect(slotsOf((await galleryPage(env, { kind: 0, limit: 20, withheld: true, segments: [[{ by: 'claimed', key: 8, ids: after.here.map(r => r.id) }]] })).ids)).toEqual([1]);
    // In year order too, a claimed crop takes its place among the family's own.
    const dated = await galleryPage(env, { kind: 0, limit: 20, withheld: true, order: 'year', segments: [[{ by: 'char', key: 7, except: [ids[3]] }, { by: 'claimed', key: 8, ids: [ids[3]] }]] });
    expect(slotsOf(dated.ids)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 9, 8]);
  });
  it('are read from the claims\' own index', () => {
    const { db } = claimed();
    const plan = (db.query(`EXPLAIN QUERY PLAN ${filedClaimsQuery()}`).all() as { detail: string }[]).map(r => r.detail).join(' | ');
    expect(plan).toContain('current_claim_object');
    expect(plan).toContain('INTEGER PRIMARY KEY');
  });
});

describe('new-layout counts', () => {
  it('add the reviews since the publication to its answer', async () => {
    const { db, env } = setup();
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,style,state,quiz,decade,n) VALUES(3,0,7,-1,1,-1,0,0,1,1700,5),(4,0,7,-1,1,-1,0,0,1,1700,-1),
      (4,0,7,-1,1,-1,0,1,1,-1,1),(4,1,-1,9,1,-1,2,0,1,-1,2)`);
    const cells = (await characterCounts(env, [7, 9])).get(7)!;
    expect(cells.filter(c => !c.fresh).map(c => c.n)).toEqual([3, 2]);
    // Only generation 4 counts: generation 3 is in the answer already.
    expect(cells.filter(c => c.fresh).map(c => [c.state, c.decade, c.n])).toEqual([[0, 1700, -1], [1, null, 1]]);
    expect(cells.reduce((sum, c) => sum + c.n, 0)).toBe(5);
    expect((await characterCounts(env, [9])).get(9)).toEqual([]);
    expect((await filedCounts(env, [7])).get(7)).toEqual([]);
    // The crops with no character are read by family from the rows of character -1.
    expect((await filedCounts(env, [9])).get(9)!.map(c => [c.kind, c.n, c.fresh])).toEqual([[1, 2, true]]);
  });

  it('read the pointer again after a minute', async () => {
    const { env, objects } = setup();
    expect((await currentPointer(env, 4e12)).pub).toBe('P1');
    objects.set('v3/pointer.json', gzipSync(JSON.stringify({ pub: 'P2', through_gen: 4 })));
    expect((await currentPointer(env, 4e12 + 30_000)).pub).toBe('P1');
    expect((await currentPointer(env, 4e12 + 61_000)).pub).toBe('P2');
  });
});
