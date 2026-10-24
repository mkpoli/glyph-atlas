import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from '../forms.test';
import { gzipSync } from 'node:zlib';
import { publicId } from '../atlas3';
import { legacyKey } from '../reviewEvidence';
import { decodeCursor, PAGE, reviewsPage, reviewsQuery } from './reviews';

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

  it('reads a moved review of a crop on a page past 2^7 from its own archive', async () => {
    const { add, archive, env, reads } = setup();
    const id = 200 * 2 ** 24 + 5;
    archive(200, { 'legacy:old-9': { event: { id: 'old-9' } } });
    add(1, id, 1, 'old-9');
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect(review.event).toEqual({ id: 'old-9' });
    expect(reads).toEqual([legacyKey(200)]);
  });

  it('pages in commit order, so a review committed with an earlier time past the cursor is still read', async () => {
    const { db, add, archive, env } = setup(), records: Record<string, any> = {};
    for (let id = 1; id <= PAGE + 1; id++) records[`legacy:old-${id}`] = { event: { id: `old-${id}` } };
    archive(1, records);
    for (let id = 1; id <= PAGE; id++) add(id, 16777216 + id, 1, `old-${id}`);
    const first = await reviewsPage(env, new URLSearchParams());
    // Stamped before the last page's reviews, committed after them.
    add(PAGE + 1, 16777216 + PAGE + 1, 1, `old-${PAGE + 1}`);
    db.exec(`UPDATE events SET at=0 WHERE id=${PAGE + 1}`);
    const last = await reviewsPage(env, new URLSearchParams({ after: first.next ?? btoa(JSON.stringify([PAGE])) }));
    expect(last.reviews.map(r => r.event.id)).toEqual([`old-${PAGE + 1}`]);
  });

  it('reads reviews through their own index, not past marks', () => {
    const { db } = setup();
    const plan = (db.query('EXPLAIN QUERY PLAN ' + reviewsQuery()).all(0, 1) as { detail: string }[]).map(r => r.detail);
    expect(plan.some(d => d.includes('event_reviews'))).toBe(true);
  });

  it('names the crop and the reason of a review saved before evidence was kept', async () => {
    const { db, add, crop, env } = setup();
    crop(16777217, 1);
    add(1, 16777217, 1, null);
    db.exec("INSERT INTO pages(id,key,book,rev,next_slot) VALUES(1,'p',1,1,2)");
    const objects = (env as any).MEDIA;
    const bytes = gzipSync(JSON.stringify({ key: 'p', book: 1, rev: 1, page: null, crops: [{ slot: 1, id: 'ex:a' }] }));
    await objects.put('v3/pages/1-1.json.gz', bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect(review).toMatchObject({ event: { id: 'v3:1', target_id: 'ex:a' }, unavailable: 'saved before review evidence was kept' });
  });

  it('counts neither a mark nor a re-cut that cleared it, and neither supersedes a review', async () => {
    const { add, crop, archive, env } = setup();
    archive(1, { 'legacy:old-1': { event: { id: 'old-1' } } });
    add(1, 16777217, 1, 'old-1');
    add(2, 16777217, 2, null, 3);
    add(3, 16777217, 3, null, 7);
    crop(16777217, 3);
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect([review.current, review.current_revision]).toEqual([true, 1]);
  });

  it('asks for a restart when given a cursor of another form', async () => {
    const { env } = setup();
    await expect(reviewsPage(env, new URLSearchParams({ after: btoa(JSON.stringify([1, 2])) }))).rejects.toThrow('Restart the export');
  });

  it('advances the cursor over a completely hidden legacy page', async () => {
    const { env, add, archive } = setup(), records: Record<string, any> = {};
    for (let id = 1; id <= PAGE + 1; id++) records[`legacy:old-${id}`] = { legacy_hidden: true, event: { id: `old-${id}` } };
    archive(1, records);
    for (let id = 1; id <= PAGE + 1; id++) add(id, 16777216 + id, 1, `old-${id}`);
    const first = await reviewsPage(env, new URLSearchParams());
    expect(first.reviews).toEqual([]);
    expect(decodeCursor(first.next)).toEqual({ id: PAGE });
    const last = await reviewsPage(env, new URLSearchParams({ after: first.next! }));
    expect(last.reviews).toEqual([]);
    expect(last.next).toBeNull();
  });

  it('lists a review whose record is missing or unreadable as unavailable, and serves the rest of the page', async () => {
    const { db, env, add, crop, archive, objects } = setup();
    const legacy = 'cf:12494629-322d-44f2-930d-78fecbf63608';
    archive(1, { 'legacy:old-1': { event: { id: 'old-1' } } });
    add(1, 16777217, 1, 'old-1');
    add(2, 16777218, 1, legacy); // moved after the archive was built
    crop(16777218, 1);
    add(3, 33554433, 1, 'old-3'); // a page with no archive
    add(4, 16777219, 1, null);
    db.exec(`UPDATE submissions SET evidence='${'a'.repeat(64)}' WHERE id=4`); // its object is absent
    add(5, 16777220, 1, null);
    db.exec(`UPDATE submissions SET evidence='${'b'.repeat(64)}' WHERE id=5`);
    objects.set(`v3/review-evidence/${'b'.repeat(64)}.json.gz`, new TextEncoder().encode('not gzip').buffer as ArrayBuffer);
    archive(1, { 'legacy:old-1': { event: { id: 'old-1' } }, 'legacy:old-6': { event: { id: 'old-6' } } });
    add(6, 16777221, 1, 'old-6');
    const { reviews, next } = await reviewsPage(env, new URLSearchParams());
    expect(next).toBeNull();
    expect(reviews.map(r => [r.event.id, r.unavailable])).toEqual([
      ['old-1', undefined],
      [legacy, 'The old-site archive of its page does not hold it.'],
      ['old-3', 'The old-site archive of its page is missing.'],
      ['v3:4', 'Review evidence is unavailable.'],
      ['v3:5', 'The review evidence object could not be read.'],
      ['old-6', undefined],
    ]);
    expect(reviews[1]).toMatchObject({ evidence: null, crop: publicId(16777218), current: true, current_revision: 1 });
  });

  it('fails the page when R2 does not answer, so the export is retried', async () => {
    const { env, add, archive } = setup();
    archive(1, { 'legacy:old-1': { event: { id: 'old-1' } } });
    add(1, 16777217, 1, 'old-1');
    (env as any).MEDIA.get = async () => { throw new Error('R2 unavailable'); };
    await expect(reviewsPage(env, new URLSearchParams())).rejects.toThrow('R2 unavailable');
  });
});
