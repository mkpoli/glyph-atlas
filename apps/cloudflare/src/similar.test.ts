import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from './forms.test';
import { bundleKey, cropId, cropItems, publicId } from './atlas3';
import { aliasCrops } from './atlas3Aliases';
import { pageRevQuery, resetPointer, shardOf, similarCrops, textIdOf } from './similar';
import { resetPointer as resetOldPointer, similarCrops as oldSimilarCrops } from './routes/fixtures/similar.old';

// The Workers cache, as far as alias reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  delete: async (request: Request) => edge.delete(request.url),
} };
const databases: Database[] = [];
beforeEach(() => { edge.clear(); resetPointer(); resetOldPointer(); });
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

const gz = (value: unknown) => gzipSync(JSON.stringify(value));
function bucket(objects: Map<string, Uint8Array>, reads: string[] = []) {
  return { get: async (key: string) => {
    reads.push(key);
    const bytes = objects.get(key);
    return bytes ? { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer,
      json: async () => JSON.parse(new TextDecoder().decode(bytes)) } : null;
  } };
}

let pages = 7000;
// One page of crops in glyph-atlas-v3 and the neighbour lists of its first crop, keyed by text id as
// published: a live crop, a retired one, one named only by a redirect, and one the alias map lacks.
async function setup() {
  const db = new Database(':memory:'); databases.push(db);
  db.exec(readFileSync(new URL('../migrations-atlas/0001_init.sql', import.meta.url), 'utf8'));
  // Each test takes a page and text ids of its own, since bundles and alias shards are held per isolate.
  const page = ++pages, n = page;
  const crop = (slot: number) => cropId(page, slot);
  const text = (name: string) => `hk:${n}:${name}`;
  db.exec(`INSERT INTO books VALUES(1,'hk:book'); INSERT INTO graphemes(id,text) VALUES(1,'あ'),(2,'い');
    INSERT INTO codes VALUES('state',0,'pending');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'hk:book:${n}',1,3,5);
    INSERT INTO crops(id,kind,book,char,state,quiz,shuffle) VALUES(${crop(0)},0,1,1,0,1,1),(${crop(1)},0,1,2,0,1,2),
      (${crop(3)},0,1,1,0,1,3);`);
  const aliases: Record<string, number> = { [text('self')]: crop(0), [text('near')]: crop(1), [text('retired')]: crop(2),
    [text('far')]: crop(3), [text('redirected')]: crop(1) };
  const shards = new Map<string, Record<string, number>>();
  for (const [t, id] of Object.entries(aliases)) {
    const name = `v3/aliases/${createHash('sha256').update(t).digest('hex').slice(0, 3)}.json.gz`;
    shards.set(name, { ...shards.get(name), [t]: id });
  }
  const objects = new Map<string, Uint8Array>([...shards].map(([name, shard]) => [name, gz(shard)]));
  objects.set(bundleKey(page, 3), gz({ key: `hk:book:${n}`, book: 1, rev: 3, page: null, crops: [
    { slot: 0, id: text('self'), label: 'あ' }, { slot: 1, id: text('near'), label: 'い' },
    { slot: 2, id: text('retired'), label: 'あ', retired: true }, { slot: 3, id: text('far'), label: 'あ' }] }));
  const lists = { [text('self')]: {
    similar: [[text('near'), 0.95], [text('redirected'), 0.94], [text('retired'), 0.93], [text('unknown'), 0.92], [text('far'), 0.9]],
    filed_differently: [[text('near'), 0.95]] } };
  objects.set('similar/current.json', new TextEncoder().encode(JSON.stringify({ revision: 'r1', shard_digits: 2 })));
  objects.set(`similar/r1/${await shardOf(text('self'), 2)}.json.gz`, gz(lists));
  const reads: string[] = [];
  const env = { ATLAS: d1(db), MEDIA: bucket(objects, reads) } as unknown as Env;
  return { db, env, crop, text, reads, objects };
}

describe('similar crops', () => {
  it('finds the crop’s lists by its text id and answers each neighbour by public id, with its score', async () => {
    const { env, crop, text } = await setup();
    expect(await textIdOf(env, crop(0))).toBe(text('self'));
    const result = await similarCrops(env, text('self'), 12, cropItems);
    expect(result.revision).toBe('r1');
    // A retired crop, a redirect source and a text id the map lacks are left out, as the old route left out
    // ids it held no unit for.
    expect(result.similar.map((i: any) => [i.id, i.label, i.score])).toEqual([[publicId(crop(1)), 'い', 0.95], [publicId(crop(3)), 'あ', 0.9]]);
    expect(result.filed_differently.map((i: any) => i.id)).toEqual([publicId(crop(1))]);
  });

  it('looks up each text id once, a shard at a time, and stops at the limit', async () => {
    const { env, crop, text } = await setup();
    const asked: number[][] = [];
    const items = async (e: Env, ids: number[]) => { asked.push([...ids]); return cropItems(e, ids); };
    const one = await similarCrops(env, text('self'), 1, items);
    expect(one.similar.map((i: any) => i.id)).toEqual([publicId(crop(1))]);
    // The first list's first id answers it; the second list's only id is already known.
    expect(asked).toEqual([[crop(1)]]);
    const found = await aliasCrops(env, [text('near'), text('far'), text('near'), text('unknown')]);
    expect([...found]).toEqual([[text('near'), crop(1)], [text('far'), crop(3)]]);
  });

  it('answers empty lists before any revision is published, and for a crop no shard lists', async () => {
    const { env, text, objects } = await setup();
    expect(await similarCrops(env, null, 12, cropItems)).toEqual({ revision: 'r1', similar: [], filed_differently: [] });
    expect(await similarCrops(env, text('far'), 12, cropItems)).toEqual({ revision: 'r1', similar: [], filed_differently: [] });
    resetPointer();
    objects.delete('similar/current.json');
    expect(await similarCrops(env, text('self'), 12, cropItems)).toEqual({ revision: null, similar: [], filed_differently: [] });
  });

  it('answers as the old reader did, after its text ids are mapped to public ids', async () => {
    const { env, text } = await setup();
    // The old database held a unit for each crop the new layout holds live, by text id.
    const live = new Set([text('self'), text('near'), text('far')]);
    const old = await oldSimilarCrops(env, text('self'), 12, async (_, ids) =>
      new Map(ids.filter(id => live.has(id)).map(id => [id, { id }])));
    const now = await similarCrops(env, text('self'), 12, cropItems);
    const mapped = await aliasCrops(env, [...live]);
    for (const list of ['similar', 'filed_differently'])
      expect(now[list].map((i: any) => [i.id, i.score])).toEqual(old[list].map((i: any) => [publicId(mapped.get(i.id)!), i.score]));
  });

  it('is routed by public id, and an old text id moves to it', async () => {
    const { env, crop, text } = await setup();
    const get = async (id: string) => (await import('./index')).default.fetch(
      new Request(`https://glyphatlas.org/atlas/characters/${encodeURIComponent(id)}/similar?limit=1`), env, { waitUntil: () => {} } as unknown as ExecutionContext);
    const shown = await get(publicId(crop(0)));
    expect(shown.status).toBe(200);
    expect((await shown.json()).similar.map((i: any) => i.id)).toEqual([publicId(crop(1))]);
    const moved = await get(text('self'));
    expect([moved.status, moved.headers.get('location')])
      .toEqual([302, `https://glyphatlas.org/atlas/characters/${publicId(crop(0))}/similar?limit=1`]);
    // A crop the site does not hold has no neighbours, as before.
    const none = await get(publicId(crop(9)));
    expect([none.status, await none.json()]).toEqual([200, { revision: 'r1', similar: [], filed_differently: [] }]);
  }, 30000);

  it('reads a crop’s page revision by primary key', async () => {
    const { db } = await setup();
    const plan = db.query(`EXPLAIN QUERY PLAN ${pageRevQuery()}`).all(1) as { detail: string }[];
    expect(plan.map(r => r.detail).join(' ')).toMatch(/SEARCH pages USING INTEGER PRIMARY KEY/);
  });

  it('shards by the SHA-256 of the id, as the Python side does', async () => {
    // hashlib.sha256(b"hi:34000001").hexdigest()[:3]
    expect(await shardOf('hi:34000001', 3)).toBe(Bun.CryptoHasher.hash('sha256', 'hi:34000001', 'hex').slice(0, 3));
  });
});
