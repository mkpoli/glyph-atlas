import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from '../forms.test';
import { gzipSync } from 'node:zlib';
import { publicId } from '../atlas3';
import { legacyKey } from '../reviewEvidence';
import { decodeCursor, PAGE, reviewsPage } from './reviews';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  const directory = new URL('../../migrations-atlas/', import.meta.url);
  for (const name of readdirSync(directory).filter(name => name.endsWith('.sql')).sort())
    db.exec(readFileSync(new URL(name, directory), 'utf8'));
  db.exec("INSERT INTO actors(id,key) VALUES(1,'reviewer'); INSERT INTO imports VALUES('published',1700000000)");
  const objects = new Map<string, ArrayBuffer>(), reads: string[] = [];
  const env = { ATLAS: d1(db), MEDIA: {
    put: async (key: string, value: ArrayBuffer) => { objects.set(key, value); },
    get: async (key: string) => { reads.push(key); return objects.has(key) ? { body: new Response(objects.get(key)).body! } : null; },
  } } as unknown as Env;
  const add = (id: number, crop: number, revision: number, legacy: string | null, kind = 1) => {
    db.prepare("INSERT INTO submissions(id,key,actor,request,response,at) VALUES(?,?,1,'{}','{}',1)").run(id, `s${id}`);
    db.prepare('INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,legacy) VALUES(?,?,?,0,0,1,?,?,?,?)')
      .run(id, crop, revision, id, id, kind, legacy);
  };
  const crop = (id: number, revision: number) => db.prepare(`INSERT INTO crops(id,kind,book,state,quiz,shuffle,cut,revision)
    VALUES(?,0,1,0,1,0,0,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision`).run(id, revision);
  const archive = (page: number, reviews: Record<string, unknown>) => {
    const bytes = gzipSync(JSON.stringify({ version: 1, reviews }));
    objects.set(legacyKey(page), bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  };
  return { db, env, objects, reads, add, crop, archive };
}

describe('immutable review export', () => {
  it('returns a moved review as the old export did, current until a later review of its crop', async () => {
    const { add, crop, archive, env, reads } = setup();
    const original = { event: { id: 'old-1', target_id: 'offline:crop', at: '2026-10-01T00:00:00+00:00', evidence: '{ "reading": "か" }' },
      reviewed: { character: { id: 'offline:crop', revision: 3 } }, expected_revision: 3,
      publication_snapshot: { character: { revision: 0, page_id: 'book:p1' } } };
    archive(1, { 'legacy:old-1': original });
    crop(16777217, 5);
    add(1, 16777217, 4, 'old-1');
    add(2, 16777217, 5, null, 3); // a seen mark: the crop's revision less its marks is 4
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect(review).toEqual({ ...original, crop: publicId(16777217), current: true, current_revision: 4 });
    expect(reads).toEqual([legacyKey(1)]);
    add(3, 16777217, 6, null, 1); // a later review, saved here before evidence was kept
    crop(16777217, 6);
    const [after] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect([after.current, after.current_revision]).toEqual([false, 5]);
    expect(after.event).toEqual(original.event);
  });

  it('advances the cursor over a completely hidden legacy page', async () => {
    const { env, add, archive } = setup(), records: Record<string, any> = {};
    for (let id = 1; id <= PAGE + 1; id++) records[`legacy:old-${id}`] = { legacy_hidden: true, event: { id: `old-${id}` } };
    archive(1, records);
    for (let id = 1; id <= PAGE + 1; id++) add(id, 16777216 + id, 1, `old-${id}`);
    const first = await reviewsPage(env, new URLSearchParams());
    expect(first.reviews).toEqual([]);
    expect(decodeCursor(first.next)).toEqual({ at: PAGE, id: PAGE });
    const last = await reviewsPage(env, new URLSearchParams({ after: first.next! }));
    expect(last.reviews).toEqual([]);
    expect(last.next).toBeNull();
  });

  it('fails the whole page when an archive or a review in it is missing', async () => {
    const { env, add, archive, objects } = setup();
    archive(1, {});
    add(1, 16777217, 1, 'old-1');
    await expect(reviewsPage(env, new URLSearchParams())).rejects.toThrow('missing from');
    objects.clear();
    await expect(reviewsPage(env, new URLSearchParams())).rejects.toThrow('not archived');
  });
});
