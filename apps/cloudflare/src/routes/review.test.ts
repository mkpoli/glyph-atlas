import { nextPage } from './fixtures/pageIds';
import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { ownedActors, ownedKeys } from '../auth';
import { Problem } from '../busy';
import { body, canonical, literal, text } from '../input';
import { formOfCrop, setForm, type ClaimTools } from './claims';
import { foundCrop } from '../cropRecords';
import { STALE_CODE } from '../atlas3Crops';
import { catalogueKey, catalogueStamps } from '../atlas3Stamps';
import { reviewsPage } from './reviews';
import { reject } from './admin';
import { actOnClaim, type LedgerTools } from '../ledger';
import { rankingQuery } from './ranking';
import { followCorpus } from './forms';
import { readReviewEvidence } from '../reviewEvidence';
import { cropChanges, reviewCrops, reviewCropsQuery, reviewRoute, revert, roundMembers, roundMembersQuery, type Submission } from './review';

const H = 'a'.repeat(64), G = 'b'.repeat(64);
const databases: Database[] = [];
// Every statement a write prepares, planned as it is first seen: a table read without an index is kept here.
const scans = new Map<string, string[]>();
function planned(db: Database) {
  const atlas = d1(db);
  return { ...atlas, prepare: (sql: string) => {
    if (!scans.has(sql)) {
      const statement = db.prepare('EXPLAIN QUERY PLAN ' + sql);
      const plan = (statement.all(...Array(statement.paramsCount).fill(null)) as { detail: string }[]).map(r => r.detail);
      statement.finalize();
      // A scan of a table, not of the request's JSON list or the subquery over it (`m`).
      scans.set(sql, plan.filter(d => /^SCAN /.test(d) && !d.includes('VIRTUAL TABLE') && d !== 'SCAN m' && d !== 'SCAN CONSTANT ROW'));
    }
    return atlas.prepare(sql);
  } };
}
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

// The new layout's schema: every atlas migration, and the people port's columns until its migration lands.
function schema(db: Database) {
  const dir = new URL('../../migrations-atlas/', import.meta.url);
  const files = readdirSync(dir).filter(f => f.endsWith('.sql')).sort();
  for (const file of files) db.exec(readFileSync(new URL(file, dir), 'utf8'));
  const columns = (db.query("SELECT name FROM pragma_table_info('events')").all() as { name: string }[]).map(c => c.name);
  if (!columns.includes('shown')) db.exec('ALTER TABLE events ADD COLUMN shown INTEGER; ALTER TABLE events ADD COLUMN named INTEGER;');
  if (!db.query("SELECT 1 FROM sqlite_master WHERE name='corpus_rows'").get())
    db.exec('CREATE TABLE corpus_rows(crop INTEGER PRIMARY KEY, seq INTEGER NOT NULL UNIQUE)');
}

// One page: local crops 0–5 (は, ハ, は, は, あ, あ), corpus glyphs 6–8 (は), all pending and dealt.
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  schema(db);
  const page = nextPage(), ids = Array.from({ length: 9 }, (_, slot) => cropId(page, slot));
  db.exec(`INSERT INTO generation VALUES(0,3);
    INSERT INTO codes(field,code,key) VALUES('state',0,'pending'),('state',1,'checked'),('state',2,'flagged'),
      ('category',2,'kanji'),('category',3,'kana'),('category',4,'other'),('style',0,'cursive');
    INSERT INTO books(id,key) VALUES(1,'hk:book');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'hk:book:${page}',1,1,9);
    INSERT INTO graphemes(id,text,head) VALUES(1,'は',1),(2,'ハ',1),(3,'𛂞',1),(4,'あ',4),(5,'い',5);`);
  const chars = [1, 2, 1, 1, 4, 4, 1, 1, 1];
  ids.forEach((id, slot) => db.exec(`INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle,cut,revision)
    VALUES(${id},${slot >= 6 ? 1 : 0},1,${chars[slot]},NULL,3,0,0,1,${slot},0,0)`));
  const local = (slot: number) => ({ slot, id: `hk:${page}:${slot}`, label: '', image_sha256: H, image: `/atlas/media/${slot}.webp`,
    box: { x: 10 * slot, y: 0, w: 10, h: 10 }, context: true, context_image: '/atlas/media/ctx.webp', context_box: { x: 0, y: 0, w: 200, h: 200 }, crop_box: { x: 10 * slot, y: 0, w: 10, h: 10 },
    source_scale: [1, 1] });
  const records = [6, 7, 8].map(slot => JSON.stringify({ id: `hng:${page}:${slot}`, label: 'は', written_character: 'は', identity_status: 'assigned',
    proxyable: true, source_revision: G, image: `https://example.org/${slot}.jpg`, box: { x: 1, y: 1, w: 5, h: 5 },
    grapheme: 'U+306F', visual_group: { id: 'U+306F:01' } }));
  let offset = 0;
  const places = records.map(r => { const place = ['packs/p.bin', offset, Buffer.byteLength(r)]; offset += Buffer.byteLength(r); return place; });
  const objects = new Map<string, Uint8Array>([
    [bundleKey(page, 1), gzipSync(JSON.stringify({ key: `hk:book:${page}`, book: 1, rev: 1, page: null,
      crops: [...[0, 1, 2, 3, 4, 5].map(local), ...[6, 7, 8].map((slot, i) => ({ slot, id: `hng:${page}:${slot}`, record_place: places[i] })),
        { slot: 9, id: `hk:${page}:9`, retired: true, replaced_by: ids[0] }] }))],
    ['packs/p.bin', new TextEncoder().encode(records.join(''))]]);
  const MEDIA = { put: async (key: string, value: ArrayBuffer) => { objects.set(key, new Uint8Array(value)); }, get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    const bytes = objects.get(name);
    if (!bytes) return null;
    const part = options?.range ? bytes.subarray(options.range.offset, options.range.offset + options.range.length) : bytes;
    return { body: new Response(part).body!, arrayBuffer: async () => new Uint8Array(part).buffer };
  } };
  const env = { ATLAS: planned(db), MEDIA, CORRECTIONS: { limit: async () => ({ success: true }) }, CLAIMS: { limit: async () => ({ success: true }) } } as unknown as Env;
  return { db, env, ids, page, objects };
}
const ORIGIN = 'https://glyphatlas.org';
async function send(env: Env, path: string, body: unknown, user = 'u1') {
  const request = new Request(ORIGIN + path, { method: 'POST', body: JSON.stringify(body), headers: { origin: ORIGIN, 'content-type': 'application/json' } });
  try {
    const response = await reviewRoute(env, request, path, user);
    return { status: response!.status, body: await response!.json() as any };
  } catch (error) {
    if (error instanceof Problem) return { status: error.status, body: { detail: error.message, ...error.extra } as any };
    throw error;
  }
}
const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const local = (id: number, extra = {}) => ({ id: publicId(id), revision: 0, image_sha256: H, ...extra });
const crop = (db: Database, id: number) => db.query('SELECT char,state,revision,quiz,category FROM crops WHERE id=?').get(id) as any;
const tally = (db: Database) => Object.fromEntries((db.query('SELECT char,state,sum(n) AS n FROM tally GROUP BY char,state HAVING sum(n)<>0').all() as any[])
  .map(r => [`${r.char}:${r.state}`, r.n]));

describe('review count dimensions', () => {
  it('keeps source families and witness decades through a review and undo', async () => {
    const { db, env, ids } = setup();
    db.exec(`UPDATE crops SET family=1,year=-11 WHERE id=${ids[0]}`);
    const reviewed = await send(env, '/atlas/rounds', { id: uuid(501), grapheme: 'U+306F',
      answers: [local(ids[0], { verdict: 'wrong', issue: 'character', character: 'あ' })] });
    expect(reviewed.status).toBe(200);
    expect(db.query('SELECT char,family,decade,n FROM tally ORDER BY char').all()).toEqual([
      { char: 1, family: 1, decade: -20, n: -1 }, { char: 4, family: 1, decade: -20, n: 1 }]);
    expect((await send(env, `/atlas/rounds/${uuid(501)}/undo`, {})).status).toBe(200);
    expect(db.query('SELECT sum(abs(n)) AS n FROM tally').get()).toEqual({ n: 0 });
  });
});

describe('captured review exports', () => {
  it('freeze the crop as reviewed and as published in its page bundle, unchanged by later revisions', async () => {
    const { db, env, ids, page } = setup();
    const request = { id: uuid(710), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ' };
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, request)).status).toBe(200);
    const before = (await reviewsPage(env, new URLSearchParams())).reviews[0];
    expect(before).toMatchObject({ event: { id: expect.stringMatching(/^v3:\d+$/), target_id: `hk:${page}:0` }, expected_revision: 0, current_revision: 1,
      reviewed: { character: { id: `hk:${page}:0`, revision: 0, page_id: `hk:book:${page}` } } });
    const { slot: _slot, ...published } = { slot: 0, id: `hk:${page}:0`, label: '', image_sha256: H, image: '/atlas/media/0.webp',
      box: { x: 0, y: 0, w: 10, h: 10 }, context: true, context_image: '/atlas/media/ctx.webp', context_box: { x: 0, y: 0, w: 200, h: 200 }, crop_box: { x: 0, y: 0, w: 10, h: 10 },
      source_scale: [1, 1] };
    expect(before.publication_snapshot).toEqual({ character: { ...published, page_id: `hk:book:${page}`, revision: 0 } });
    expect(JSON.parse(before.event.evidence).request.revision).toBe(0);
    expect(JSON.parse((db.query('SELECT request FROM submissions').get() as any).request).input).toEqual(request);
    // A later page revision and later physical revisions leave the record as it was saved.
    db.prepare('UPDATE pages SET rev=2 WHERE id=?').run(page);
    db.prepare('UPDATE crops SET revision=10 WHERE id=?').run(ids[0]);
    const { current_revision: _a, ...kept } = before;
    const { current_revision: _b, ...after } = (await reviewsPage(env, new URLSearchParams())).reviews[0];
    expect(after).toEqual(kept);
  });

  it('commits no review when storing its evidence fails', async () => {
    const { db, env, ids } = setup();
    env.MEDIA = { ...env.MEDIA, put: async () => { throw new Error('evidence write failed'); } } as unknown as R2Bucket;
    await expect(send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(711), revision: 0, image_sha256: H, verdict: 'match',
    })).rejects.toThrow('evidence write failed');
    expect(db.query('SELECT * FROM submissions').all()).toEqual([]);
    expect(db.query('SELECT * FROM events').all()).toEqual([]);
  });

  it('keeps one committed evidence reference when the same request races itself', async () => {
    const { db, env, ids } = setup();
    const request = { id: uuid(712), revision: 0, image_sha256: H, verdict: 'match' };
    const [a, b] = await Promise.all([send(env, `/atlas/characters/${publicId(ids[0])}`, request), send(env, `/atlas/characters/${publicId(ids[0])}`, request)]);
    expect(a).toEqual(b);
    expect(a.status).toBe(200);
    expect(db.query('SELECT count(*) AS n FROM submissions').get()).toEqual({ n: 1 });
    expect(db.query('SELECT count(*) AS n FROM events').get()).toEqual({ n: 1 });
    expect((await reviewsPage(env, new URLSearchParams())).reviews).toHaveLength(1);
  });

  it('take the old site\'s publication record as the baseline of a crop the old site reviewed', async () => {
    const { db, env, ids, page, objects } = setup();
    const published = { character: { id: `hk:${page}:0`, label: 'は', revision: 0, box: { x: 0, y: 0, w: 10, h: 10 } } };
    db.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(90,'old',1,'{}','{}',1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,legacy) VALUES(${ids[0]},1,0,0,1,90,1,0,'old-1');
      UPDATE crops SET revision=1 WHERE id=${ids[0]}`);
    objects.set(`v3/review-legacy/${page}.json.gz`, gzipSync(JSON.stringify({ version: 1, reviews: {
      'legacy:old-1': { event: { id: 'old-1' }, expected_revision: 0, publication_snapshot: published } } })));
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(714), revision: 1, image_sha256: H, verdict: 'match' })).status).toBe(200);
    const review = (await reviewsPage(env, new URLSearchParams())).reviews.find(r => r.event.id !== 'old-1');
    expect(review).toMatchObject({ expected_revision: 1, publication_snapshot: published });
  });

  it('read each earlier evidence object once, and refuse a save to retry when one cannot be read', async () => {
    const { db, env, ids, objects } = setup();
    const crops = [ids[0], ids[2], ids[3]].map(id => local(id));
    expect((await send(env, '/atlas/rounds', { id: uuid(715), grapheme: 'U+306F',
      answers: crops.map(c => ({ ...c, verdict: 'unsure' })) })).status).toBe(200);
    const evidence = [...objects.keys()].filter(key => key.startsWith('v3/review-evidence/'));
    expect(evidence).toHaveLength(1);
    const reads: string[] = [], get = env.MEDIA.get.bind(env.MEDIA);
    (env as any).MEDIA = { ...env.MEDIA, get: async (key: string, options?: any) => { reads.push(key); return get(key, options); } };
    expect((await send(env, '/atlas/characters/@batch', { id: uuid(716), character: 'は',
      crops: crops.map(c => ({ ...c, revision: 1 })) })).status).toBe(200);
    expect(db.query('SELECT count(*) AS n FROM events').get()).toEqual({ n: 6 });
    expect(reads.filter(key => key === evidence[0])).toHaveLength(1);
    const latest = `v3/review-evidence/${(db.query('SELECT evidence FROM submissions ORDER BY id DESC LIMIT 1').get() as any).evidence}.json.gz`;
    const kept = objects.get(latest)!;
    objects.set(latest, gzipSync('{"version":1,"reviews":{}}'));
    const review = { id: uuid(717), revision: 2, image_sha256: H, verdict: 'match' };
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, review)).status).toBe(503);
    objects.set(latest, kept);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, review)).status).toBe(200);
  });

  it('build an anchored crop\'s baseline without reading its history', async () => {
    const { db, env, ids } = setup();
    // The old site reviewed it, its page has no archive, and a publication has since spoken for it.
    db.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(90,'old',1,'{}','{}',1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,legacy) VALUES(${ids[0]},1,0,0,1,90,1,0,'old-1');
      UPDATE crops SET revision=1 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},4,1)`);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(720), revision: 1, image_sha256: H, verdict: 'match' })).status).toBe(200);
    const hash = (db.query('SELECT evidence FROM submissions ORDER BY id DESC LIMIT 1').get() as any).evidence;
    expect((await readReviewEvidence(env, hash))[`crop:${ids[0]}`]).toMatchObject({ expected_revision: 4,
      publication_snapshot: { character: { revision: 4 } } });
  });

  it('refuse to save a review of a crop the old site reviewed until its page is archived', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(90,'old',1,'{}','{}',1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,legacy) VALUES(${ids[0]},1,0,0,1,90,1,0,'old-1');
      UPDATE crops SET revision=1 WHERE id=${ids[0]}`);
    const refused = await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(719), revision: 1, image_sha256: H, verdict: 'match' });
    expect([refused.status, refused.body.detail]).toEqual([503, 'The reviews moved from the old site are not archived yet.']);
    expect(db.query('SELECT count(*) AS n FROM submissions').get()).toEqual({ n: 1 });
  });

  it('leave out the page id of a crop that sits on its book rather than a page', async () => {
    const { db, env, ids, page } = setup();
    db.prepare("UPDATE pages SET key='book:hk' WHERE id=?").run(page);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(718), revision: 0, image_sha256: H, verdict: 'match' })).status).toBe(200);
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect('page_id' in review.reviewed.character).toBe(false);
    expect('page_id' in review.publication_snapshot.character).toBe(false);
  });

  it('lists a review saved before evidence was kept without a record', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(713), revision: 0, image_sha256: H, verdict: 'match' })).status).toBe(200);
    db.exec('UPDATE submissions SET evidence=NULL');
    const [review] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect(review).toMatchObject({ evidence: null, crop: publicId(ids[0]), current: true, current_revision: 1 });
    expect(review.publication_snapshot).toBeUndefined();
  });
});

describe('revisions as the offline store counts them', () => {
  it('leave out marks, and a chain of reviews under one published record shares its baseline', async () => {
    const { env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(700), grapheme: 'U+306F', seen: [local(ids[0])] })).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(701), revision: 1, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(702), revision: 2, image_sha256: H, verdict: 'match',
    })).status).toBe(200);
    const [first, second] = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect([first.expected_revision, second.expected_revision]).toEqual([0, 1]);
    expect(first.reviewed.character.revision).toBe(0);
    expect(second.publication_snapshot).toEqual(first.publication_snapshot);
    expect(first.publication_snapshot.character.revision).toBe(0);
    expect([first.current, second.current, second.current_revision]).toEqual([false, true, 2]);
  });

  it('take the revision a publication gave the crop, counting reviews since but not marks', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(704), grapheme: 'U+306F', seen: [local(ids[0])] })).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(705), revision: 1, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    // The store imported the relabel at its revision 2, and a publication spoke for the crop at that revision.
    db.prepare('INSERT INTO review_revisions(crop,revision,at) VALUES(?,2,2)').run(ids[0]);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(706), revision: 2, image_sha256: H, verdict: 'match',
    })).status).toBe(200);
    expect((await send(env, '/atlas/rounds', { id: uuid(707), grapheme: 'U+306F', seen: [local(ids[0], { revision: 3 })] })).status).toBe(200);
    const reviews = (await reviewsPage(env, new URLSearchParams())).reviews;
    expect(reviews.map(r => r.expected_revision)).toEqual([0, 2]);
    expect(reviews[1].publication_snapshot.character.revision).toBe(2);
    expect(reviews[1].current_revision).toBe(3);
  });

  it('refuse a view from before a crop was put back, though only marks came before', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(734), grapheme: 'U+306F', seen: [local(ids[0])] })).status).toBe(200);
    // Retired at 1, put back at 2 with the store's revision 0 recorded there.
    db.exec(`UPDATE crops SET revision=2, state=0 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},0,2); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,2,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
    const stale = await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(735), revision: 1, image_sha256: H, verdict: 'match' });
    expect(stale.status).toBe(409);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(736), revision: 2, image_sha256: H, verdict: 'match' })).status).toBe(200);
  });

  it('keep a view of a crop put back standing after another reader marks it', async () => {
    const { db, env, ids } = setup();
    // Reviewed at 1 in its earlier life, put back at 2.
    db.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(91,'early',1,'{}','{}',1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${ids[0]},1,0,0,1,91,1,0);
      UPDATE crops SET revision=2 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},0,2); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,2,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
    expect((await send(env, '/atlas/rounds', { id: uuid(740), grapheme: 'U+306F', seen: [local(ids[0], { revision: 2 })] }, 'u2')).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(741), revision: 2, image_sha256: H, verdict: 'match' })).status).toBe(200);
  });

  it('leave open views and undos alone after a publication that changed nothing a reviewer sees', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(742), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    expect((await send(env, '/atlas/rounds', { id: uuid(743), grapheme: 'U+3042', seen: [local(ids[0], { revision: 1 })] }, 'u2')).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[2])}`, {
      id: uuid(746), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    // The store's revision recorded at each crop's own, with nothing a reviewer sees changed.
    db.exec(`INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},2,2),(${ids[2]},2,1)`);
    // A view read after the review, before the mark, still stands; so does an undo of a review from before.
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(744), revision: 1, image_sha256: H, verdict: 'match' })).status).toBe(200);
    expect((await send(env, `/atlas/rounds/${uuid(746)}/undo`, {})).status).toBe(200);
  });

  it('record nothing for a seen mark from before a crop was put back, and save the rest of the round', async () => {
    const { db, env, ids } = setup();
    db.exec(`UPDATE crops SET revision=2 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},0,2); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,2,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
    const round = await send(env, '/atlas/rounds', { id: uuid(745), grapheme: 'U+306F',
      seen: [local(ids[0], { revision: 1 }), local(ids[2])], skipped: [local(ids[3])] });
    expect(round.status).toBe(200);
    expect(db.query('SELECT crop FROM seen').all()).toEqual([{ crop: ids[2] }]);
    expect(db.query('SELECT crop FROM skips').all()).toEqual([{ crop: ids[3] }]);
    expect(db.query('SELECT revision FROM crops WHERE id=?').get(ids[0])).toEqual({ revision: 2 });
  });

  it('record nothing for a seen crop with a boundary that names no revision, as an older client sends it', async () => {
    const { db, env, ids } = setup();
    db.exec(`UPDATE crops SET revision=2 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},0,2); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,2,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
    const { revision: _revision, ...bare } = local(ids[0], { revision: 2 });
    expect((await send(env, '/atlas/rounds', { id: uuid(747), grapheme: 'U+306F', seen: [bare, local(ids[2])] })).status).toBe(200);
    expect(db.query('SELECT crop FROM seen').all()).toEqual([{ crop: ids[2] }]);
  });

  it('record nothing for a seen crop put back between the round\'s read and its commit', async () => {
    const { db, env, ids } = setup(), atlas = env.ATLAS;
    env.ATLAS = { ...atlas, batch: async (statements: D1PreparedStatement[]) => {
      db.exec(`UPDATE crops SET revision=1 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},0,1); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,1,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
      return atlas.batch(statements);
    } } as D1Database;
    expect((await send(env, '/atlas/rounds', { id: uuid(748), grapheme: 'U+306F', seen: [local(ids[0]), local(ids[2])] })).status).toBe(200);
    expect(db.query('SELECT crop FROM seen').all()).toEqual([{ crop: ids[2] }]);
  });

  it('refuse a view or an undo from before a crop was retired and put back', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(730), grapheme: 'U+306F', seen: [local(ids[0])] })).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(732), revision: 1, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    // Retired at 2, then put back by a publication as い at the store's revision 1: a new revision, 3, records it.
    db.exec(`UPDATE crops SET revision=3, char=5, state=0 WHERE id=${ids[0]};
      INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},1,3); INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state) SELECT id,3,cut,0,0,0,1,8,char,state FROM crops WHERE id=${ids[0]}`);
    const stale = await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(733), revision: 2, image_sha256: H, verdict: 'match' });
    expect(stale.status).toBe(409);
    // The reviewer's own undo leaves the crop as the publication put it, and says so for that crop.
    const undo = await send(env, `/atlas/rounds/${uuid(732)}/undo`, {});
    expect(undo.status).toBe(200);
    expect(undo.body.results).toEqual([{ id: null, target_id: publicId(ids[0]), refused: 'This crop was published again since this review.' }]);
    expect(db.query('SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(ids[0])).toEqual({ text: 'い' });
  });

  it('let an admin reject a review a publication has since changed the crop over, leaving what the publication set', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(737), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    db.exec(`UPDATE crops SET revision=2, char=5 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},1,2);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,published) VALUES(${ids[0]},2,0,0,0,0,1,8,5,1)`);
    const actor = (key: string) => (db.query('SELECT id FROM actors WHERE key=?').get(key) as any).id;
    const own = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(737)}`) as Submission;
    const rejected = await revert(env, own, actor('u1'), 'admin', { reason: 'vandalism' });
    expect(rejected.results).toHaveLength(1);
    expect(rejected.results[0].refused).toBeUndefined();
    // The review's state goes; the character the publication set stays.
    expect(db.query('SELECT g.text,c.state FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(ids[0])).toEqual({ text: 'い', state: 0 });
    expect(db.query('SELECT undone FROM submissions WHERE key=?').get(`u1:${uuid(737)}`)).toEqual({ undone: 1 });
  });

  it('keep a submission a reviewer could undo only in part open for an admin, counting only what was undone', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(739), grapheme: 'U+306F', answers: [
      local(ids[0], { verdict: 'wrong', issue: 'character', character: 'あ' }), local(ids[2], { verdict: 'wrong', issue: 'character', character: 'あ' })] })).status).toBe(200);
    // A publication since set the state of one crop.
    db.exec(`UPDATE crops SET revision=2, state=1 WHERE id=${ids[0]};
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,state,published) VALUES(${ids[0]},2,0,0,0,0,1,8,1,2)`);
    const undo = await send(env, `/atlas/rounds/${uuid(739)}/undo`, {});
    expect(undo.body.results.map((r: any) => Boolean(r.refused))).toEqual([true, false]);
    expect(db.query('SELECT undone FROM submissions WHERE key=?').get(`u1:${uuid(739)}`)).toEqual({ undone: 0 });
    expect(db.query('SELECT reviews,rounds FROM actor_stats').get()).toEqual({ reviews: 1, rounds: 1 });
    // Asked again, the reviewer gets the same refusal, and nothing is undone twice.
    const again = await send(env, `/atlas/rounds/${uuid(739)}/undo`, {});
    expect(again.body.results).toEqual([{ id: null, target_id: publicId(ids[0]), refused: 'This crop was published again since this review.' }]);
    // An admin undoes the rest, and the submission is undone.
    const actor = (key: string) => (db.query('SELECT id FROM actors WHERE key=?').get(key) as any).id;
    const own = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(739)}`) as Submission;
    const rejected = await revert(env, own, actor('u1'), 'admin', { reason: 'vandalism' });
    expect(rejected.results.map(r => r.target_id)).toEqual([publicId(ids[0])]);
    expect(db.query('SELECT undone FROM submissions WHERE key=?').get(`u1:${uuid(739)}`)).toEqual({ undone: 1 });
    expect(db.query('SELECT reviews,rounds FROM actor_stats').get()).toEqual({ reviews: 0, rounds: 0 });
  });

  it('refuse a reviewer\'s undo where the crop moved on with no event since', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(749), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    // An older publication moved it on without an event.
    db.exec(`UPDATE crops SET revision=2 WHERE id=${ids[0]}`);
    const undo = await send(env, `/atlas/rounds/${uuid(749)}/undo`, {});
    expect(undo.body.results[0].refused).toBeDefined();
  });

  it('let a reviewer undo a review across a publication that changed nothing it set', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, {
      id: uuid(738), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: 'あ',
    })).status).toBe(200);
    // A publication that changed only whether the crop is dealt.
    db.exec(`UPDATE crops SET revision=2, quiz=0 WHERE id=${ids[0]}; INSERT INTO review_revisions(crop,revision,at) VALUES(${ids[0]},1,2);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,quiz,published) VALUES(${ids[0]},2,0,0,0,0,1,8,0,4)`);
    const undo = await send(env, `/atlas/rounds/${uuid(738)}/undo`, {});
    expect(undo.status).toBe(200);
    expect(undo.body.results[0].refused).toBeUndefined();
    // The review's character goes; the quiz the publication set stays.
    expect(db.query('SELECT g.text,c.quiz FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(ids[0])).toEqual({ text: 'は', quiz: 0 });
  });

  it('refuses a review when its page is published again between reading the crop and committing', async () => {
    const { db, env, ids, page } = setup(), atlas = env.ATLAS;
    env.ATLAS = { ...atlas, batch: async (statements: D1PreparedStatement[]) => {
      db.prepare('UPDATE pages SET rev=rev+1 WHERE id=?').run(page);
      return atlas.batch(statements);
    } } as D1Database;
    const refused = await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(703), revision: 0, image_sha256: H, verdict: 'match' });
    expect([refused.status, refused.body.detail]).toEqual([409, "This crop's page was published again. Reload it."]);
    expect(db.query('SELECT * FROM submissions').all()).toEqual([]);
    expect(db.query('SELECT * FROM events').all()).toEqual([]);
  });
});

describe('a round', () => {
  it('reviews its answers, marks what it showed, and answers as the old site did', async () => {
    const { db, env, ids } = setup();
    const round = { id: uuid(1), grapheme: 'U+306F', answers: [local(ids[0], { verdict: 'wrong', issue: 'character', character: 'あ' })],
      seen: [local(ids[1])], skipped: [local(ids[2])] };
    const { status, body } = await send(env, '/atlas/rounds', round);
    expect(status).toBe(200);
    const event = (db.query('SELECT id FROM events WHERE crop=? AND kind=1').get(ids[0]) as any).id;
    expect(body).toEqual({ id: uuid(1), results: [
      { id: event, target_id: publicId(ids[0]), field: 'review', revision: 1, state: 'checked' },
      { target_id: publicId(ids[1]), field: 'seen' }, { target_id: publicId(ids[2]), field: 'skip' }] });
    // The named character is the crop's label; the shown crop is `seen`; one skip marks nothing.
    expect(crop(db, ids[0])).toEqual({ char: 4, state: 1, revision: 1, quiz: 1, category: 3 });
    expect(crop(db, ids[1])).toEqual({ char: 2, state: 3, revision: 1, quiz: 1, category: 3 });
    expect(crop(db, ids[2])).toEqual({ char: 1, state: 0, revision: 0, quiz: 1, category: 3 });
    expect(db.query('SELECT crop,revision,kind,label,state,issue,shown,named,was_state FROM events ORDER BY id').all()).toEqual([
      { crop: ids[0], revision: 1, kind: 1, label: 4, state: 1, issue: 0, shown: 1, named: 4, was_state: 0 },
      { crop: ids[1], revision: 1, kind: 3, label: 2, state: 3, issue: null, shown: 2, named: null, was_state: 0 }]);
    // Counts move from the old cells to the new ones, in the current generation.
    expect(tally(db)).toEqual({ '1:0': -1, '4:1': 1, '2:0': -1, '2:3': 1 });
    expect(db.query('SELECT DISTINCT gen FROM tally').all()).toEqual([{ gen: 3 }]);
    expect(db.query('SELECT crop,char,state,base_char,base_state FROM reviews ORDER BY crop').all()).toEqual([
      { crop: ids[0], char: 4, state: 1, base_char: 1, base_state: 0 }, { crop: ids[1], char: 2, state: 3, base_char: 2, base_state: 0 }]);
    expect(db.query('SELECT reviews,rounds FROM actor_stats').all()).toEqual([{ reviews: 1, rounds: 1 }]);
    // The stored response is the one answered; a retry gets it again, a changed retry is refused.
    expect(JSON.parse((db.query('SELECT response FROM submissions').get() as any).response)).toEqual(body);
    expect((await send(env, '/atlas/rounds', round)).body).toEqual(body);
    expect((await send(env, '/atlas/rounds', { ...round, answers: [] })).status).toBe(409);
  });

  it('marks a crop hard once two reviewers skipped it, and leaves out a crop that changed', async () => {
    const { db, env, ids } = setup();
    await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', skipped: [local(ids[2])] }, 'u1');
    expect(crop(db, ids[2]).state).toBe(0);
    const second = await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', skipped: [local(ids[2])],
      seen: [local(ids[3], { image_sha256: G }), local(ids[4])] }, 'u2');
    // ids[3] shows other pixels and ids[4] is not one of は's characters: neither was seen as it stands.
    expect(second.body.results).toEqual([{ target_id: publicId(ids[2]), field: 'skip' }]);
    expect(crop(db, ids[2])).toMatchObject({ state: 4, revision: 1 });
    expect(db.query('SELECT kind,state FROM events').all()).toEqual([{ kind: 4, state: 4 }]);
    // A reviewer's second skip of a crop replaces their first.
    await send(env, '/atlas/rounds', { id: uuid(3), grapheme: 'U+306F', skipped: [local(ids[2], { revision: 1 })] }, 'u2');
    expect(db.query('SELECT count(*) AS n FROM skips').get()).toEqual({ n: 2 });
  });

  it('saves a member’s round with that member’s crops alone, and refuses a crop of another grapheme', async () => {
    const { db, env, ids } = setup();
    const kept = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+30CF', seen: [local(ids[0]), local(ids[1])] });
    expect([kept.status, kept.body.results.length]).toEqual([200, 1]);
    const other = await send(env, '/atlas/rounds', { id: uuid(3), grapheme: 'U+30CF', answers: [local(ids[0], { verdict: 'unsure' })] });
    expect(other).toEqual({ status: 409, body: { detail: 'This round changed. Reload it.' } });
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(90,'春',90),(91,'萅',90),(92,'\u{3D307}',90)`);
    expect(await roundMembers(env, 'U+3D307')).toEqual(['\u{3D307}']);
    expect(await roundMembers(env, 'U+6625')).toEqual(['春', '萅', '\u{3D307}']);
    expect(await roundMembers(env, 'U+30C4 U+309A')).toEqual(['ツ゚']);
    const wrong = await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', answers: [local(ids[4], { verdict: 'unsure' })] });
    expect(wrong).toEqual({ status: 409, body: { detail: 'This round changed. Reload it.' } });
  });

  it('refuses a stale revision, and a write that loses the race to another', async () => {
    const { db, env, ids } = setup();
    const stale = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', answers: [local(ids[0], { revision: 3, verdict: 'unsure' })] });
    expect(stale).toEqual({ status: 409, body: { detail: 'This character changed. Reload it.' } });
    // Another write moves the crop between this one's read and its batch.
    const batch = env.ATLAS.batch.bind(env.ATLAS);
    (env.ATLAS as any).batch = async (list: any[]) => { db.exec(`UPDATE crops SET revision=revision+1 WHERE id=${ids[0]}`); return batch(list); };
    const lost = await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', answers: [local(ids[0], { verdict: 'unsure' })] });
    expect(lost).toEqual({ status: 409, body: { detail: 'Another review changed this crop. Reload it.' } });
    expect(db.query('SELECT count(*) AS n FROM submissions').get()).toEqual({ n: 0 });
  });

  it('takes a crop\'s revision from before marks only, as the old site never moved it for them', async () => {
    const { db, env, ids } = setup();
    await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', seen: [local(ids[3])] }, 'u2');
    expect(crop(db, ids[3]).revision).toBe(1);
    // The inspector opened before the round still saves; the answer names the crop's revision now.
    const saved = await send(env, `/atlas/characters/${publicId(ids[3])}`, { ...local(ids[3]), id: uuid(2), verdict: 'unsure' });
    expect(saved.body.results[0]).toMatchObject({ revision: 2, state: 'flagged' });
    // A review is a change: the revision before it no longer stands.
    expect((await send(env, `/atlas/characters/${publicId(ids[3])}`, { ...local(ids[3], { revision: 1 }), id: uuid(3), verdict: 'match' })).status).toBe(409);
    // So is a revision a publication moved without an event.
    await send(env, '/atlas/rounds', { id: uuid(4), grapheme: 'U+306F', seen: [local(ids[2])] }, 'u2');
    db.exec(`UPDATE crops SET revision=revision+1 WHERE id=${ids[2]}`);
    expect((await send(env, `/atlas/characters/${publicId(ids[2])}`, { ...local(ids[2]), id: uuid(5), verdict: 'match' })).status).toBe(409);
    expect((await send(env, `/atlas/characters/${publicId(ids[2])}`, { ...local(ids[2], { revision: 2 }), id: uuid(6), verdict: 'match' })).status).toBe(200);
  });

  it('saves again on a fresh read when only a mark moved the crop between its read and its batch', async () => {
    const { db, env, ids } = setup();
    const batch = env.ATLAS.batch.bind(env.ATLAS);
    let raced = 0;
    // Another round marks the crop seen once, just before this write's batch.
    (env.ATLAS as any).batch = async (list: any[]) => {
      if (!raced++) db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,state) VALUES(${ids[0]},1,0,3,9,9,1,3,3);
        UPDATE crops SET state=3,revision=1 WHERE id=${ids[0]}`);
      return batch(list);
    };
    const saved = await send(env, `/atlas/characters/${publicId(ids[0])}`, { ...local(ids[0]), id: uuid(1), verdict: 'unsure' });
    expect(saved.body.results[0]).toMatchObject({ revision: 2, state: 'flagged' });
    expect(crop(db, ids[0])).toMatchObject({ state: 2, revision: 2 });
    // A review in between is a change: refused.
    raced = 0;
    (env.ATLAS as any).batch = async (list: any[]) => {
      if (!raced++) db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,state) VALUES(${ids[1]},1,0,3,9,9,1,0,1);
        UPDATE crops SET state=1,revision=1 WHERE id=${ids[1]}`);
      return batch(list);
    };
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}`, { ...local(ids[1]), id: uuid(2), verdict: 'unsure' })).status).toBe(409);
  });

  it.each(['batch', 'single'])('keeps the %s response when a mark forces a retry', async mode => {
    const { db, env, ids } = setup();
    const id = ids[mode === 'batch' ? 0 : 6], batch = env.ATLAS.batch.bind(env.ATLAS);
    let raced = false;
    (env.ATLAS as any).batch = async (list: any[]) => {
      if (!raced) {
        raced = true;
        db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,state) VALUES(${id},1,0,3,9,9,1,3,3);
          UPDATE crops SET state=3,revision=1 WHERE id=${id}`);
      }
      return batch(list);
    };
    const path = mode === 'batch' ? '/atlas/corrections' : `/atlas/characters/${publicId(id)}`;
    const input = mode === 'batch' ? { id: uuid(1), character: 'は', crops: [local(id)] }
      : { id: uuid(1), revision: 0, source_revision: G, verdict: 'match' };
    const saved = await send(env, path, input);
    expect(saved.status).toBe(200);
    if (mode === 'batch') expect(saved.body).toMatchObject({ id: uuid(1), unchanged: [], results: [{ target_id: publicId(id), state: 'checked' }] });
    else expect(saved.body).toMatchObject({ id: uuid(1), results: [{ target_id: publicId(id), state: 'checked', revision: 2 }] });
    expect((await send(env, path, input)).body).toEqual(saved.body);
  });

  it('drops observations if a cut, character or eligibility changes before commit', async () => {
    const { db, env, ids } = setup();
    const batch = env.ATLAS.batch.bind(env.ATLAS);
    let raced = false;
    (env.ATLAS as any).batch = async (list: any[]) => {
      if (!raced) {
        raced = true;
        db.exec(`UPDATE crops SET cut=1 WHERE id=${ids[0]}; UPDATE crops SET quiz=0 WHERE id=${ids[1]};
          UPDATE crops SET char=4 WHERE id=${ids[2]}`);
      }
      return batch(list);
    };
    const saved = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F',
      seen: [local(ids[0]), local(ids[1]), local(ids[3])], skipped: [local(ids[2])] });
    expect(saved).toEqual({ status: 200, body: { id: uuid(1), results: [{ target_id: publicId(ids[3]), field: 'seen' }] } });
    expect(db.query('SELECT crop FROM seen').all()).toEqual([{ crop: ids[3] }]);
    expect(db.query('SELECT crop FROM skips').all()).toEqual([]);
    expect(db.query('SELECT crop,cut FROM events').all()).toEqual([{ crop: ids[3], cut: 0 }]);
  });

  it('answers a retired crop with the one that replaced it', async () => {
    const { env, ids, page } = setup();
    const retired = publicId(cropId(page, 9));
    expect(await send(env, `/atlas/characters/${retired}`, { ...local(cropId(page, 9)), id: uuid(1), verdict: 'match' }))
      .toEqual({ status: 404, body: { detail: 'This crop was replaced.', replaced_by: publicId(ids[0]) } });
    // A round's seen crop that is gone is only left out.
    const round = await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', seen: [local(cropId(page, 9)), local(ids[1])] });
    expect(round.body.results).toEqual([{ target_id: publicId(ids[1]), field: 'seen' }]);
  });

  it('answers a text id the alias map does not hold with a prompt to reload', async () => {
    const { env } = setup();
    const result = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', seen: [{ id: 'hk:nowhere:1', image_sha256: H }] });
    expect(result.status).toBe(409);
    expect(result.body.code).toBe(STALE_CODE);
  });
});

describe('an undo', () => {
  it.each(['marks', 'unchanged'])('counts concurrent undo only once for a %s-only submission', async mode => {
    const { db, env, ids } = setup();
    if (mode === 'marks') await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', seen: [local(ids[0])] });
    else {
      db.query('UPDATE crops SET state=1 WHERE id=?').run(ids[0]);
      await send(env, '/atlas/corrections', { id: uuid(1), character: 'は', crops: [local(ids[0])] });
    }
    const batch = env.ATLAS.batch.bind(env.ATLAS), other = { ...env, ATLAS: { ...env.ATLAS, batch } } as Env;
    const path = `/atlas/rounds/${uuid(1)}/undo`;
    let raced = false;
    (env.ATLAS as any).batch = async (list: any[]) => {
      if (!raced) {
        raced = true;
        expect((await send(other, path, {})).status).toBe(200);
      }
      return batch(list);
    };
    expect(await send(env, path, {})).toEqual({ status: 200, body: { id: uuid(1), results: [], duplicate: true } });
    expect(db.query('SELECT reviews,rounds FROM actor_stats').all()).toEqual([{ reviews: 0, rounds: 0 }]);
    expect(db.query('SELECT count(*) AS n FROM events WHERE kind=5').get()).toEqual({ n: mode === 'marks' ? 1 : 0 });
    expect(db.query(`SELECT value FROM site WHERE key='undos'`).get()).toEqual({ value: '1' });
  });

  it('moves the catalogue stamps when it changes no crop, once', async () => {
    const { db, env, ids } = setup();
    db.query('UPDATE crops SET state=1 WHERE id=?').run(ids[0]);
    await send(env, '/atlas/corrections', { id: uuid(1), character: 'は', crops: [local(ids[0])] });
    const fresh = { ...env, CATALOGUE_LAG: '0' } as Env, path = `/atlas/rounds/${uuid(1)}/undo`;
    const before = await catalogueStamps(fresh);
    expect((await send(env, path, {})).status).toBe(200);
    const after = await catalogueStamps(fresh);
    expect(after).toEqual({ ...before!, undos: 1 });
    expect(catalogueKey(after)).not.toBe(catalogueKey(before));
    // Asked again, the submission is undone already and is not counted twice.
    expect((await send(env, path, {})).body.duplicate).toBe(true);
    expect(await catalogueStamps(fresh)).toEqual(after);
  });

  it('puts back what the round changed, and its marks', async () => {
    const { db, env, ids } = setup();
    await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', skipped: [local(ids[2])] }, 'u2');
    await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', answers: [local(ids[0], { verdict: 'wrong', issue: 'crop' })],
      seen: [local(ids[1])], skipped: [local(ids[2])] }, 'u1');
    expect([crop(db, ids[0]).state, crop(db, ids[1]).state, crop(db, ids[2]).state]).toEqual([2, 3, 4]);
    const undone = await send(env, `/atlas/rounds/${uuid(1)}/undo`, {}, 'u1');
    expect(undone.status).toBe(200);
    const review = db.query('SELECT id FROM events WHERE crop=? AND kind=1').get(ids[0]) as any;
    const undo = db.query('SELECT id,revision,kind,label,state,issue,shown,named,reverses,quiz FROM events WHERE crop=? AND kind=5').get(ids[0]) as any;
    expect(undo).toMatchObject({ revision: 2, label: 1, state: 0, issue: null, shown: 1, named: null, reverses: review.id, quiz: 1 });
    expect(undone.body).toEqual({ id: uuid(1), results: [{ id: undo.id, target_id: publicId(ids[0]), revision: 2,
      review: { id: undo.id, kind: 'undo', reverses: review.id, at: expect.any(String) } }] });
    // Each crop is as before the round: the review undone, the seen mark gone, one skipper left.
    expect(crop(db, ids[0])).toMatchObject({ char: 1, state: 0, revision: 2 });
    expect(crop(db, ids[1])).toMatchObject({ state: 0, revision: 2 });
    expect(crop(db, ids[2])).toMatchObject({ state: 0, revision: 2 });
    const seenMark = db.query('SELECT id FROM events WHERE crop=? AND kind=3').get(ids[1]) as any;
    expect(db.query('SELECT reverses FROM events WHERE crop=? AND kind=5').get(ids[1])).toEqual({ reverses: seenMark.id });
    // The hard mark was this round's, its second skip.
    const hardMark = db.query('SELECT id FROM events WHERE crop=? AND kind=4').get(ids[2]) as any;
    expect(db.query('SELECT reverses FROM events WHERE crop=? AND kind=5').get(ids[2])).toEqual({ reverses: hardMark.id });
    expect(tally(db)).toEqual({});
    expect(db.query('SELECT a.key,s.reviews,s.rounds FROM actor_stats s JOIN actors a ON a.id=s.actor ORDER BY a.key').all())
      .toEqual([{ key: 'u1', reviews: 0, rounds: 0 }, { key: 'u2', reviews: 0, rounds: 1 }]);
    // Undone twice, it answers as a duplicate; another reviewer has no such round.
    expect((await send(env, `/atlas/rounds/${uuid(1)}/undo`, {}, 'u1')).body).toEqual({ id: uuid(1), results: [], duplicate: true });
    expect((await send(env, `/atlas/rounds/${uuid(1)}/undo`, {}, 'u2')).status).toBe(404);
  });

  it('refuses an admin\'s corpus flags, which are no review', async () => {
    const { db, env } = setup();
    db.exec(`INSERT OR IGNORE INTO actors(key,user,via) VALUES('u1','u1','account');
      INSERT INTO submissions(key,actor,request,response,at) SELECT 'u1:${uuid(1)}',id,'{"input":{},"target":"@flags"}','{}',1 FROM actors WHERE key='u1'`);
    expect(await send(env, `/atlas/rounds/${uuid(1)}/undo`, {})).toEqual({ status: 409, body: { detail: 'A flag is not a review, so it cannot be undone.' } });
  });

  it('restores a mark another round still holds, and a redrawn box it replaced', async () => {
    const { db, env, ids } = setup();
    await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', seen: [local(ids[3])] }, 'u2');
    const box = { x: 30, y: 1, w: 12, h: 9 };
    const path = `/atlas/characters/${publicId(ids[3])}`;
    const fixed = await send(env, path, { ...local(ids[3], { revision: 1 }), id: uuid(2), verdict: 'match', box });
    expect(fixed.body.detail).toBeUndefined();
    expect(fixed.status).toBe(200);
    expect(db.query('SELECT x,y,w,h FROM reviews WHERE crop=?').get(ids[3])).toEqual(box);
    expect(crop(db, ids[3])).toMatchObject({ state: 1, revision: 2 });
    await send(env, `/atlas/corrections/${uuid(2)}/undo`, {});
    // Back to the mark the other round left, with the cut's own box.
    expect(crop(db, ids[3])).toMatchObject({ state: 3, revision: 3 });
    expect(db.query('SELECT x,y,w,h,state FROM reviews WHERE crop=?').get(ids[3])).toEqual({ x: null, y: null, w: null, h: null, state: 3 });
  });

  it('is refused once a later review changed the crop, unless an admin passes over the reviewer\'s own undone work', async () => {
    const { db, env, ids } = setup();
    const review = (n: number, revision: number, answer: object, user: string, path = 'atlas/characters') =>
      send(env, `/${path}/${publicId(ids[0])}`, { ...local(ids[0], { revision }), id: uuid(n), ...answer }, user);
    expect((await review(1, 0, { verdict: 'unsure' }, 'u1')).status).toBe(200);
    expect((await review(2, 1, { verdict: 'wrong', issue: 'blank' }, 'u1', 'layers/units')).status).toBe(200);
    expect((await send(env, `/atlas/corrections/${uuid(2)}/undo`, {}, 'u1')).status).toBe(200);
    expect(crop(db, ids[0])).toMatchObject({ state: 2, revision: 3 });
    expect(await send(env, `/atlas/corrections/${uuid(1)}/undo`, {}, 'u1'))
      .toEqual({ status: 409, body: { detail: 'A later review changed this crop. It cannot be undone.' } });
    const actor = (key: string) => (db.query('SELECT id FROM actors WHERE key=?').get(key) as any).id;
    const own = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(1)}`) as Submission;
    // The reviewer's own later review, undone, and the undos themselves do not.
    const rejected = await revert(env, own, actor('u1'), 'admin', { reason: 'r', reviewer: [actor('u1')] });
    expect(rejected.results).toHaveLength(1);
    expect(db.query('SELECT by,reason FROM rejections').all()).toEqual([{ by: 'admin', reason: 'r' }]);
    expect(crop(db, ids[0])).toMatchObject({ char: 1, state: 0, revision: 4 });
    expect(db.query('SELECT undone FROM submissions WHERE key=?').get(`u1:${uuid(1)}`)).toEqual({ undone: 1 });
    // Anyone else's later change stops it, even undone, as before.
    expect((await review(3, 4, { verdict: 'unsure' }, 'u1')).status).toBe(200);
    expect((await review(4, 5, { verdict: 'match' }, 'u2')).status).toBe(200);
    expect((await send(env, `/atlas/corrections/${uuid(4)}/undo`, {}, 'u2')).status).toBe(200);
    const third = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(3)}`) as Submission;
    await expect(revert(env, third, actor('u2'), 'admin', { reason: 'r', reviewer: [actor('u1')] })).rejects.toThrow('later review');
  });
});

describe('a batch correction', () => {
  it('names the character on the crops that need it, confirms those written so, and refuses a changed one', async () => {
    const { db, env, ids } = setup();
    const ok = await send(env, '/atlas/corrections', { id: uuid(1), character: 'あ', crops: [local(ids[0]), local(ids[4])] });
    expect(ok.body).toEqual({ id: uuid(1), results: [{ target_id: publicId(ids[0]), revision: 1, state: 'checked' },
      { target_id: publicId(ids[4]), revision: 1, state: 'checked' }], unchanged: [] });
    expect(db.query('SELECT kind,named FROM events ORDER BY id').all()).toEqual([{ kind: 1, named: 4 }, { kind: 0, named: null }]);
    const again = await send(env, '/atlas/corrections', { id: uuid(2), character: 'あ', crops: [local(ids[4], { revision: 1 })] });
    expect(again.body).toEqual({ id: uuid(2), results: [], unchanged: [publicId(ids[4])] });
    const refused = await send(env, '/atlas/corrections', { id: uuid(3), character: 'い', crops: [local(ids[5], { revision: 4 })] });
    expect(refused).toEqual({ status: 409, body: { detail: 'Some of these crops changed or were already checked. Reload them.',
      targets: [{ id: publicId(ids[5]), reason: 'changed' }] } });
  });

  it('judges crops a listing just showed by their entries at the edge, without reading their page', async () => {
    const { db, env, ids, page, objects } = setup();
    const entry = (slot: number) => ({ slot, id: `hk:${page}:${slot}`, label: '', image_sha256: H, image: `/atlas/media/${slot}.webp`,
      box: { x: 10 * slot, y: 0, w: 10, h: 10 }, context: true, context_image: '/atlas/media/ctx.webp', context_box: { x: 0, y: 0, w: 200, h: 200 }, crop_box: { x: 10 * slot, y: 0, w: 10, h: 10 },
      source_scale: [1, 1] });
    const edge = new Map([0, 4].map(slot => [`${ORIGIN}/_entries/${page}-1/${slot}`, entry(slot)]));
    objects.delete(bundleKey(page, 1));
    const caches = (globalThis as any).caches;
    (globalThis as any).caches = { default: { match: async (request: Request) => edge.has(request.url) ? Response.json(edge.get(request.url)) : undefined, put: async () => {} } };
    try {
      const ok = await send(env, '/atlas/corrections', { id: uuid(1), character: 'あ', crops: [local(ids[0]), local(ids[4])] });
      expect(ok.status).toBe(200);
      expect(ok.body.results.map((r: any) => r.target_id)).toEqual([publicId(ids[0]), publicId(ids[4])]);
      expect(crop(db, ids[0])).toMatchObject({ char: 4, state: 1, revision: 1 });
    } finally { (globalThis as any).caches = caches; }
  });
});

describe('a bad crop beside what the crop holds', () => {
  const box = { x: 30, y: 1, w: 12, h: 9 };
  const path = (id: number) => `/atlas/characters/${publicId(id)}`;
  const issues = (db: Database, n: number) => db.query('SELECT kind,issue,kept_issue,text FROM events WHERE submission=(SELECT id FROM submissions WHERE key=?)').get(`u1:${uuid(n)}`);

  it('keeps a named character and leaves the crop reported for its box', async () => {
    const { db, env, ids } = setup();
    const saved = await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'character', character: 'あ', bad_crop: true });
    expect(saved.body.results[0].state).toBe('flagged');
    expect(issues(db, 1)).toEqual({ kind: 1, issue: 0, kept_issue: 2, text: null });
    expect(crop(db, ids[0])).toMatchObject({ char: 4, state: 2 });
    expect(((await reviewCrops(env, [ids[0]])).get(ids[0]) as any).data).toMatchObject({ label: 'あ', state: 'flagged', issue: 'crop' });
  });

  it('keeps what a joined crop holds beside a bad crop', async () => {
    const { db, env, ids } = setup();
    expect((await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'merged', correction: 'あい', bad_crop: true })).status).toBe(200);
    expect(issues(db, 1)).toEqual({ kind: 1, issue: 1, kept_issue: 2, text: 'あい' });
  });

  it('takes a round\'s crop reported for both, and an undo restores it', async () => {
    const { db, env, ids } = setup();
    const round = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F', answers: [{ ...local(ids[0]), verdict: 'wrong', issue: 'character', character: 'あ', bad_crop: true }] });
    expect(round.status).toBe(200);
    expect(crop(db, ids[0])).toMatchObject({ char: 4, state: 2 });
    await send(env, `/atlas/rounds/${uuid(1)}/undo`, {});
    expect(crop(db, ids[0]).state).toBe(0);
    expect(crop(db, ids[0]).char).not.toBe(4);
  });

  it('checks a crop whose character is named and whose box is redrawn in one save', async () => {
    const { db, env, ids } = setup();
    const saved = await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'character', character: 'あ', box });
    expect(saved.body.results[0].state).toBe('checked');
    expect(crop(db, ids[0])).toMatchObject({ char: 4, state: 1 });
    expect(db.query('SELECT x,y,w,h FROM reviews WHERE crop=?').get(ids[0])).toEqual(box);
  });

  it('leaves a joined crop reported when its box is redrawn', async () => {
    const { db, env, ids } = setup();
    const saved = await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'merged', correction: 'あい', box });
    expect(saved.body.results[0].state).toBe('flagged');
    expect(db.query('SELECT x,y,w,h FROM reviews WHERE crop=?').get(ids[0])).toEqual(box);
  });

  it.each([
    ['alone', { verdict: 'wrong', issue: 'crop', bad_crop: true }],
    ['beside a blank', { verdict: 'wrong', issue: 'blank', bad_crop: true }],
    ['on a match', { verdict: 'match', bad_crop: true }],
    ['with a redrawn box', { verdict: 'wrong', issue: 'character', character: 'あ', bad_crop: true, box }],
    ['as anything but true', { verdict: 'wrong', issue: 'character', character: 'あ', bad_crop: false }],
  ])('refuses one %s', async (_, answer) => {
    const { env, ids } = setup();
    expect((await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), ...answer })).body.detail).toBe('A bad crop is reported beside a wrong or joined character.');
  });

  it('leaves a corpus glyph named beside a bad crop flagged for its box', async () => {
    const { env, ids, objects } = setup();
    const saved = await send(env, `/atlas/characters/${publicId(ids[6])}`, { id: uuid(1), revision: 0, source_revision: G,
      verdict: 'wrong', issue: 'character', character: 'ハ', bad_crop: true });
    expect(saved.body).toMatchObject({ results: [{ target_id: publicId(ids[6]), state: 'flagged', revision: 1 }] });
    // The training rules read a corpus review from its record's `new`.
    const stored = [...objects.entries()].find(([key]) => key.startsWith('v3/review-evidence/'))![1] as Uint8Array;
    const { reviews } = JSON.parse(new TextDecoder().decode(Bun.gunzipSync(stored as Uint8Array<ArrayBuffer>)));
    expect((Object.values(reviews) as any[]).map(record => record.event.new.kept_issue)).toEqual(['crop']);
  });

  it('refuses a redrawn box beside a blank', async () => {
    const { env, ids } = setup();
    expect((await send(env, path(ids[0]), { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'blank', box })).body.detail).toBe('A redrawn crop is saved as fixed.');
  });
});

describe('a crop\'s issue', () => {
  it('stays the one it was reported for when a batch names only its character, and the event keeps the answer', async () => {
    const { db, env, ids } = setup();
    await send(env, `/atlas/characters/${publicId(ids[0])}`, { ...local(ids[0]), id: uuid(1), verdict: 'wrong', issue: 'crop' });
    await send(env, `/atlas/characters/${publicId(ids[2])}`, { ...local(ids[2]), id: uuid(2), verdict: 'wrong', issue: 'blank' });
    const batch = await send(env, '/atlas/corrections', { id: uuid(3), character: 'は', crops: [local(ids[0], { revision: 1 }), local(ids[2], { revision: 1 })] });
    expect(batch.status).toBe(200);
    // Each answer is recorded as given (a match for the crop written so); each crop stays reported.
    expect(db.query('SELECT crop,kind,issue,kept_issue FROM events WHERE submission=(SELECT id FROM submissions WHERE key=?) ORDER BY id').all(`u1:${uuid(3)}`))
      .toEqual([{ crop: ids[0], kind: 0, issue: null, kept_issue: 2 }, { crop: ids[2], kind: 0, issue: null, kept_issue: 3 }]);
    const read = await reviewCrops(env, [ids[0], ids[2]]);
    expect([read.get(ids[0]), read.get(ids[2])].map((c: any) => [c.data.state, c.data.issue])).toEqual([['flagged', 'crop'], ['flagged', 'blank']]);
    // Undone, the crop is reported for its box again.
    await send(env, `/atlas/corrections/${uuid(3)}/undo`, {});
    expect(((await reviewCrops(env, [ids[0]])).get(ids[0]) as any).data.issue).toBe('crop');
  });

  it('reads the old name `reading` as `character`', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,state,issue) VALUES(${ids[0]},1,0,0,1,1,1,1,2,6);
      UPDATE crops SET state=2,revision=1 WHERE id=${ids[0]}`);
    expect(((await reviewCrops(env, [ids[0]])).get(ids[0]) as any).data.issue).toBe('character');
  });
});

describe('a corpus glyph\'s review', () => {
  it('keeps a saved form across review writes and submission retries', async () => {
    const { db, env, ids } = setup();
    const tools: ClaimTools = { body, canonical: value => canonical(value), literal, text,
      fail: (status, message, extra) => { throw new Problem(status, message, extra); },
      integer: () => { throw new Error('No pagination in a form write.'); } };
    // Claims have their own query-plan checks; this suite tracks the review write and persisted form.
    const picked = await setForm({ ...env, ATLAS: d1(db) }, { id: uuid(10), form: '𛂞', crop_version: `${publicId(ids[6])}@${G}@1,1,5,5` },
      publicId(ids[6]), 'u1', tools);
    expect(picked.form?.values[0].text).toBe('𛂞');
    const input = { id: uuid(11), revision: 0, source_revision: G, verdict: 'match' };
    for (let attempt = 0; attempt < 2; attempt++) {
      const reviewed = await send(env, `/atlas/characters/${publicId(ids[6])}`, input);
      expect(reviewed.status).toBe(200);
      expect(reviewed.body).toMatchObject({ id: uuid(11), results: [{ target_id: publicId(ids[6]), state: 'checked', revision: 1 }] });
    }
    const found = await foundCrop(env, ids[6]);
    expect(found!.record.crop_version).toBe(`${publicId(ids[6])}@${G}@1,1,5,5`);
    expect((await formOfCrop(env, ids[6]))).toEqual(picked.form);
  });

  it('updates state and written identity cleanly, and numbers corpus glyphs in the order the old site wrote their rows', async () => {
    const { db, env, ids } = setup();
    const glyph = (id: number, extra = {}) => ({ id: publicId(id), revision: 0, source_revision: G, ...extra });
    const reviewed = await send(env, `/atlas/characters/${publicId(ids[6])}`, { id: uuid(1), revision: 0, source_revision: G,
      verdict: 'wrong', issue: 'character', character: 'ハ' });
    expect(reviewed.status).toBe(200);
    expect(reviewed.body).toMatchObject({ id: uuid(1), results: [{ target_id: publicId(ids[6]), revision: 1, state: 'checked' }] });
    const found = await foundCrop(env, ids[6]);
    expect(found!.record).toMatchObject({ id: publicId(ids[6]), origin: 'corpus', label: 'ハ', state: 'checked', revision: 1,
      written_character: 'ハ', identity_status: 'assigned', grapheme: 'U+306F', visual_group: null });
    await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', answers: [glyph(ids[8], { verdict: 'unsure' })],
      seen: [glyph(ids[7])], skipped: [glyph(ids[6], { revision: 1 })] });
    expect(db.query('SELECT crop FROM corpus_rows ORDER BY seq').all().map((r: any) => r.crop)).toEqual([ids[6], ids[8], ids[7]]);
  });

  it('redraws a corpus glyph with context via /atlas/characters/:id, captures recrop/proposed_box, and undoes cleanly', async () => {
    const { db, env, objects, page } = setup();
    const ctxPage = nextPage(), gid = cropId(ctxPage, 0), uid = cropId(ctxPage, 1), fid = cropId(ctxPage, 2);
    const box = { x: 1, y: 1, w: 5, h: 5 }, nextBox = { x: 4, y: 6, w: 12, h: 14 };
    const ctxRec = JSON.stringify({ id: 'krm:ctx:0', label: 'は', written_character: 'は', identity_status: 'assigned',
      proxyable: true, source_revision: G, image: 'https://example.org/0.jpg', context_image: 'https://example.org/ctx.jpg',
      box, crop_box: box, context_box: { x: 0, y: 0, w: 200, h: 200 }, grapheme: 'U+306F' });
    const unRec = JSON.stringify({ id: 'krm:unassigned:1', label: 'Unassigned', written_character: null, identity_status: 'unassigned',
      proxyable: true, source_revision: G, image: 'https://example.org/1.jpg', context_image: 'https://example.org/ctx.jpg',
      box, crop_box: box, context_box: { x: 0, y: 0, w: 200, h: 200 } });
    const fRec = unRec.replace('krm:unassigned:1', 'krm:unassigned:2');
    const b0 = Buffer.byteLength(ctxRec), b1 = Buffer.byteLength(unRec), b2 = Buffer.byteLength(fRec);
    objects.set('packs/ctx.bin', new TextEncoder().encode(ctxRec + unRec + fRec));
    objects.set(bundleKey(ctxPage, 1), gzipSync(JSON.stringify({ key: `krm:book:${ctxPage}`, book: 1, rev: 1, page: null,
      crops: [{ slot: 0, id: 'krm:ctx:0', record_place: ['packs/ctx.bin', 0, b0] },
        { slot: 1, id: 'krm:unassigned:1', record_place: ['packs/ctx.bin', b0, b1] },
        { slot: 2, id: 'krm:unassigned:2', record_place: ['packs/ctx.bin', b0 + b1, b2] }] })));
    db.exec(`INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${ctxPage},'krm:book:${ctxPage}',1,1,3);
      INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle,cut,revision) VALUES
        (${gid},1,1,1,NULL,3,0,0,1,20,0,0),(${uid},1,1,NULL,NULL,NULL,0,0,1,21,0,0),(${fid},1,1,NULL,NULL,NULL,0,0,1,22,0,0);
      INSERT INTO form_clusters(id,key,family,label,count,coherence,shape_position,size_position,list,form) VALUES(9,'named','U+306E','の',1,1,1,1,'named','𛂜');
      UPDATE crops SET cluster=9 WHERE id=${fid}`);

    // A corpus glyph without source context still cannot be redrawn.
    const noCtx = cropId(page, 6);
    expect((await send(env, `/atlas/characters/${publicId(noCtx)}`, { id: uuid(21), revision: 0, source_revision: G, verdict: 'match', box: nextBox })).status).toBe(422);

    // An unassigned corpus glyph rejects match, even with a redrawn box, until a character is named.
    expect((await send(env, `/atlas/characters/${publicId(uid)}`, { id: uuid(22), revision: 0, source_revision: G, verdict: 'match', box: nextBox })).status).toBe(422);
    // One the forms tool named, through its cluster, is judged as its inspector shows it: a match stands.
    // A view from before the cluster was decided again names a character the glyph no longer shows.
    expect((await send(env, `/atlas/characters/${publicId(fid)}`, { id: uuid(29), revision: 0, source_revision: G, verdict: 'match', shown: '𛂞' })).status).toBe(409);
    const named = await send(env, `/atlas/characters/${publicId(fid)}`, { id: uuid(28), revision: 0, source_revision: G, verdict: 'match', shown: '𛂜' });
    expect(named.status).toBe(200);
    expect(named.body).toMatchObject({ results: [{ target_id: publicId(fid), revision: 1, state: 'checked' }] });
    // The match keeps the character it confirmed, whatever the cluster is decided as later.
    expect(db.query('SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(fid)).toEqual({ text: '𛂜' });
    db.exec(`UPDATE form_clusters SET form='𛂞' WHERE id=9`);
    expect((await foundCrop(env, fid))!.record).toMatchObject({ written_character: '𛂜', identity_basis: 'human_review' });
    const assigned = await send(env, `/atlas/characters/${publicId(uid)}`, { id: uuid(23), revision: 0, source_revision: G,
      verdict: 'wrong', issue: 'character', character: 'ハ', box: nextBox });
    expect(assigned.status).toBe(200);
    expect(db.query('SELECT x,y,w,h,state FROM reviews WHERE crop=?').get(uid)).toEqual({ ...nextBox, state: 1 });

    // A corpus glyph with context accepts a standalone box redraw on /atlas/characters/:id and records evidence.
    const redrawn = await send(env, `/atlas/characters/${publicId(gid)}`, { id: uuid(24), revision: 0, source_revision: G,
      verdict: 'match', box: nextBox });
    expect(redrawn.status).toBe(200);
    expect(db.query('SELECT x,y,w,h,state FROM reviews WHERE crop=?').get(gid)).toEqual({ ...nextBox, state: 1 });
    const stored = [...objects.entries()].filter(([k]) => k.startsWith('v3/review-evidence/')).at(-1)![1] as Uint8Array;
    const captured = Object.values(JSON.parse(new TextDecoder().decode(Bun.gunzipSync(stored as Uint8Array<ArrayBuffer>))).reviews)[0] as any;
    expect(JSON.parse(captured.event.evidence).recrop).toEqual({ from: box, to: nextBox, pixels: G });
    expect(captured.source_update.proposed_box).toEqual(nextBox);

    // A second review (with another box or without a box) preserves the source's published box in
    // `reviewed.box` and `source_update.box`, while carrying the active proposed box in `proposed_box`.
    const followBox = { x: 2, y: 2, w: 10, h: 10 };
    const secondRedraw = await send(env, `/atlas/characters/${publicId(gid)}`, { id: uuid(25), revision: 1, source_revision: G,
      verdict: 'wrong', issue: 'merged', correction: 'はハ', box: followBox });
    expect(secondRedraw.status).toBe(200);
    expect(secondRedraw.body).toMatchObject({ results: [{ target_id: publicId(gid), state: 'flagged', revision: 2 }] });
    const stored2 = [...objects.entries()].filter(([k]) => k.startsWith('v3/review-evidence/')).at(-1)![1] as Uint8Array;
    const captured2 = Object.values(JSON.parse(new TextDecoder().decode(Bun.gunzipSync(stored2 as Uint8Array<ArrayBuffer>))).reviews)[0] as any;
    expect(captured2.reviewed.box).toEqual(box);
    expect(captured2.reviewed.box_pending).toBeUndefined();
    expect(captured2.source_update.box).toEqual(box);
    expect(captured2.source_update.proposed_box).toEqual(followBox);

    // Undoing restores the box to before the latest redraw, and a non-box review keeps that pending box in evidence.
    expect((await send(env, `/atlas/rounds/${uuid(25)}/undo`, {})).status).toBe(200);
    expect(db.query('SELECT x,y,w,h,state FROM reviews WHERE crop=?').get(gid)).toEqual({ ...nextBox, state: 1 });
    const relabel = await send(env, `/atlas/characters/${publicId(gid)}`, { id: uuid(26), revision: 3, source_revision: G,
      verdict: 'wrong', issue: 'character', character: 'ハ' });
    expect(relabel.status).toBe(200);
    const stored3 = [...objects.entries()].filter(([k]) => k.startsWith('v3/review-evidence/')).at(-1)![1] as Uint8Array;
    const captured3 = Object.values(JSON.parse(new TextDecoder().decode(Bun.gunzipSync(stored3 as Uint8Array<ArrayBuffer>))).reviews)[0] as any;
    expect(captured3.reviewed.box).toEqual(box);
    expect(captured3.source_update.box).toEqual(box);
    expect(captured3.source_update.proposed_box).toEqual(nextBox);
  });
});

describe('a crop\'s style', () => {
  // The styles a publication has numbered; a style none has used yet is refused.
  const styledSetup = () => { const made = setup(); made.db.exec(`INSERT INTO codes(field,code,key) VALUES('style',1,'regular'),('style',2,'running'),('style',3,'seal')`); return made };
  const styleOf = (db: Database, id: number) => db.query(`SELECT c.revision,s.key AS style,r.style AS overlay,b.key AS base FROM crops c
    LEFT JOIN codes s ON s.field='style' AND s.code=c.style LEFT JOIN reviews r ON r.crop=c.id
    LEFT JOIN codes b ON b.field='style' AND b.code=r.base_style WHERE c.id=?`).get(id) as any;
  const styles = (db: Database) => Object.fromEntries((db.query(`SELECT s.key,sum(t.n) AS n FROM tally t JOIN codes s ON s.field='style' AND s.code=t.style
    GROUP BY s.key HAVING sum(t.n)<>0`).all() as any[]).map(r => [r.key, r.n]));

  it('lists the crop under the style a reviewer gave it, and its record says so', async () => {
    const { db, env, ids } = styledSetup();
    const saved = await send(env, `/atlas/characters/${publicId(ids[0])}/style`, { id: uuid(901), revision: 0, style: 'regular' });
    expect(saved.status).toBe(200);
    expect(saved.body).toMatchObject({ id: publicId(ids[0]), style: 'regular', style_basis: 'unit', style_inherited: 'cursive', style_editable: true, revision: 1 });
    expect(styleOf(db, ids[0])).toEqual({ revision: 1, style: 'regular', overlay: (db.query(`SELECT code FROM codes WHERE field='style' AND key='regular'`).get() as any).code, base: 'cursive' });
    expect(styles(db)).toEqual({ cursive: -1, regular: 1 });
    expect(db.query('SELECT kind,style IS NOT NULL AS own,label,state FROM events WHERE crop=?').get(ids[0])).toEqual({ kind: 9, own: 1, label: 1, state: 0 });
    // The character, state and quiz are the crop's as they were.
    expect(crop(db, ids[0])).toMatchObject({ char: 1, state: 0, quiz: 1 });
  });

  it('takes the crop back to its publication\'s style, and a retry answers with the crop as it is', async () => {
    const { db, env, ids } = styledSetup();
    await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(902), revision: 0, style: 'running' });
    await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(903), revision: 1, style: 'seal' });
    const back = await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(904), revision: 2, style: 'unassessed' });
    expect(back.body).toMatchObject({ style: 'cursive', style_basis: 'none', style_inherited: 'cursive', revision: 3 });
    expect(styleOf(db, ids[6])).toMatchObject({ revision: 3, style: 'cursive', base: 'cursive' });
    expect(styles(db)).toEqual({});
    const again = await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(904), revision: 2, style: 'unassessed' });
    expect(again).toMatchObject({ status: 200, body: { style: 'cursive', revision: 3 } });
    expect((await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(904), revision: 2, style: 'seal' })).status).toBe(409);
    expect(db.query('SELECT count(*) AS n FROM events WHERE crop=?').get(ids[6])).toEqual({ n: 3 });
  });

  it('refuses a crop changed since it was read, and a style the vocabulary does not hold', async () => {
    const { db, env, ids } = styledSetup();
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}/style`, { id: uuid(905), revision: 0, style: 'regular' })).status).toBe(200);
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}/style`, { id: uuid(906), revision: 0, style: 'seal' })).status).toBe(409);
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}/style`, { id: uuid(907), revision: 1, style: 'italic' })).status).toBe(422);
    expect(styleOf(db, ids[1])).toMatchObject({ revision: 1, style: 'regular' });
    // Clerical script is in the vocabulary, but no publication has numbered it.
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}/style`, { id: uuid(922), revision: 1, style: 'clerical' })).status).toBe(422);
    expect(db.query(`SELECT count(*) AS n FROM codes WHERE field='style'`).get()).toEqual({ n: 4 });
  });

  it('keeps the crop\'s open issue, and the store\'s count of its revisions', async () => {
    const { db, env, ids } = styledSetup();
    expect((await send(env, `/atlas/characters/${publicId(ids[3])}`, { ...local(ids[3]), id: uuid(911), verdict: 'wrong', issue: 'crop' })).status).toBe(200);
    const styled = await send(env, `/atlas/characters/${publicId(ids[3])}/style`, { id: uuid(912), revision: 1, style: 'regular' });
    expect(styled.body).toMatchObject({ state: 'flagged', issue: 'crop', revision: 2 });
    const page = await reviewsPage(env, new URLSearchParams());
    expect(page.reviews.map((r: any) => [r.expected_revision, r.current_revision])).toEqual([[0, 1]]);
    // A review after it names the crop's revision and is the next one the store counts.
    expect((await send(env, `/atlas/characters/${publicId(ids[3])}`, { ...local(ids[3], { revision: 2 }), id: uuid(913), verdict: 'match' })).status).toBe(200);
    expect((await reviewsPage(env, new URLSearchParams())).reviews.map((r: any) => [r.expected_revision, r.current_revision])).toEqual([[0, 2], [1, 2]]);
    expect(db.query('SELECT revision FROM crops WHERE id=?').get(ids[3])).toEqual({ revision: 3 });
  });

  it('leaves a corpus glyph following its cluster\'s form', async () => {
    const { db, env, ids } = styledSetup();
    await send(env, `/atlas/characters/${publicId(ids[6])}/style`, { id: uuid(915), revision: 0, style: 'regular' });
    db.exec(`INSERT INTO form_clusters(id,key,family,label,count,coherence,shape_position,size_position,list,form) VALUES(1,'test','U+306F','は',2,1,1,1,'test','𛂞');
      UPDATE crops SET cluster=1 WHERE id IN (${ids[6]},${ids[7]}); INSERT INTO corpus_follow VALUES(${ids[6]}),(${ids[7]})`);
    // The follow's own statements are not this route's, so they run outside the scan check.
    await followCorpus({ ...env, ATLAS: d1(db) } as Env);
    expect(db.query('SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id IN (?,?) ORDER BY c.id').all(ids[6], ids[7]))
      .toEqual([{ text: '𛂞' }, { text: '𛂞' }]);
  });

  it('takes the crop back to the style its publication gives it now', async () => {
    const { db, env, ids } = styledSetup();
    await send(env, `/atlas/characters/${publicId(ids[5])}/style`, { id: uuid(916), revision: 0, style: 'regular' });
    await send(env, `/atlas/characters/${publicId(ids[5])}/style`, { id: uuid(917), revision: 1, style: 'unassessed' });
    // A publication then gives the crop's document seal script.
    db.exec(`UPDATE crops SET style=3 WHERE id=${ids[5]}`);
    await send(env, `/atlas/characters/${publicId(ids[5])}/style`, { id: uuid(918), revision: 2, style: 'running' });
    const back = await send(env, `/atlas/characters/${publicId(ids[5])}/style`, { id: uuid(919), revision: 3, style: 'unassessed' });
    expect(back.body).toMatchObject({ style: 'seal', style_basis: 'none' });
  });

  it('is no submission its reviewer can undo, and leaves an earlier review undoable', async () => {
    const { db, env, ids } = styledSetup();
    expect((await send(env, `/atlas/characters/${publicId(ids[1])}`, { ...local(ids[1]), id: uuid(920), verdict: 'wrong', issue: 'character', character: 'あ' })).status).toBe(200);
    await send(env, `/atlas/characters/${publicId(ids[1])}/style`, { id: uuid(921), revision: 1, style: 'seal' }, 'u2');
    expect((await send(env, `/atlas/rounds/${uuid(921)}/undo`, {}, 'u2')).status).toBe(409);
    // Another reviewer's style since does not hold back the undo of the review; the style stays.
    expect((await send(env, `/atlas/rounds/${uuid(920)}/undo`, {})).status).toBe(200);
    expect(crop(db, ids[1])).toMatchObject({ char: 2 });
    expect(styleOf(db, ids[1])).toMatchObject({ style: 'seal' });
  });

  it('is no review an admin can reject', async () => {
    const { db, env, ids } = styledSetup();
    await send(env, `/atlas/characters/${publicId(ids[4])}/style`, { id: uuid(914), revision: 0, style: 'seal' });
    await expect(reject(env, 'admin', { submissions: ['u1:' + uuid(914)] }, { text } as any)).rejects.toThrow('A style is not a review');
    expect(styleOf(db, ids[4])).toMatchObject({ style: 'seal' });
  });

  it('a later review keeps the style and its publication\'s', async () => {
    const { db, env, ids } = styledSetup();
    await send(env, `/atlas/characters/${publicId(ids[2])}/style`, { id: uuid(908), revision: 0, style: 'regular' });
    const reviewed = await send(env, `/atlas/characters/${publicId(ids[2])}`, { id: uuid(909), revision: 1, image_sha256: H, verdict: 'match' });
    expect(reviewed.status).toBe(200);
    expect(styleOf(db, ids[2])).toMatchObject({ revision: 2, style: 'regular', base: 'cursive' });
    const back = await send(env, `/atlas/characters/${publicId(ids[2])}/style`, { id: uuid(910), revision: 2, style: 'unassessed' });
    expect(back.body).toMatchObject({ style: 'cursive', state: 'checked' });
  });
});

describe('the list of touched crops', () => {
  const touched = (db: Database) => Object.fromEntries((db.query('SELECT crop,gen FROM touched').all() as any[]).map(r => [r.crop, r.gen]));

  it('names the crops a round reviewed and marked in the current generation, and an undo in a later one moves them to it', async () => {
    const { db, env, ids } = setup();
    const round = await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+306F',
      answers: [local(ids[0], { verdict: 'wrong', issue: 'character', character: 'あ' })], seen: [local(ids[1])], skipped: [local(ids[2])] });
    expect(round.status).toBe(200);
    // The reviewed crop and the one marked seen; a single skip changes no row.
    expect(touched(db)).toEqual({ [ids[0]]: 3, [ids[1]]: 3 });
    db.exec('UPDATE generation SET gen=4');
    expect((await send(env, `/atlas/characters/${publicId(ids[3])}`, { ...local(ids[3]), id: uuid(2), verdict: 'match' })).status).toBe(200);
    expect(touched(db)).toEqual({ [ids[0]]: 3, [ids[1]]: 3, [ids[3]]: 4 });
    expect((await send(env, `/atlas/rounds/${uuid(1)}/undo`, {})).status).toBe(200);
    expect(touched(db)).toEqual({ [ids[0]]: 4, [ids[1]]: 4, [ids[3]]: 4 });
  });

  it('names a crop whose style a reviewer changed, and keeps one row for a crop changed twice', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO codes(field,code,key) VALUES('style',1,'regular')`);
    expect((await send(env, `/atlas/characters/${publicId(ids[4])}/style`, { id: uuid(3), revision: 0, style: 'regular' })).status).toBe(200);
    expect(touched(db)).toEqual({ [ids[4]]: 3 });
    expect((await send(env, `/atlas/characters/${publicId(ids[4])}/style`, { id: uuid(4), revision: 1, style: 'unassessed' })).status).toBe(200);
    expect(touched(db)).toEqual({ [ids[4]]: 3 });
    db.exec('UPDATE generation SET gen=5');
    expect((await send(env, `/atlas/characters/${publicId(ids[4])}/style`, { id: uuid(5), revision: 2, style: 'regular' })).status).toBe(200);
    expect(touched(db)).toEqual({ [ids[4]]: 5 });
  });

  it('names a corpus glyph that took its cluster\'s form, and not one already written so', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO form_clusters(id,key,family,label,count,coherence,shape_position,size_position,list,form) VALUES(1,'test','U+306F','は',2,1,1,1,'test','𛂞');
      UPDATE crops SET cluster=1 WHERE id IN (${ids[6]},${ids[7]}); UPDATE crops SET char=3 WHERE id=${ids[7]};
      INSERT INTO corpus_follow VALUES(${ids[6]}),(${ids[7]})`);
    await followCorpus({ ...env, ATLAS: d1(db) } as Env);
    expect(db.query('SELECT id,char,bumps FROM crops WHERE id IN (?,?) ORDER BY id').all(ids[6], ids[7]))
      .toEqual([{ id: ids[6], char: 3, bumps: 1 }, { id: ids[7], char: 3, bumps: 0 }]);
    expect(touched(db)).toEqual({ [ids[6]]: 3 });
  });

  it('names the crops of a change list written outside a round, as an admin\'s flag is', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO actors(id,key,user,via) VALUES(1,'admin','admin','account');
      INSERT INTO submissions(key,actor,request,response,at) VALUES('admin:f',1,'{}','{}',5)`);
    // The flag's event kind (FLAG_KIND in routes/admin.ts).
    await env.ATLAS.batch(cropChanges(env, 1, 'admin:f', 5, [6, 7].map(slot => ({ crop: ids[slot], expected: 0, cut: 0, kind: 6, label: 'は', state: 2, issue: 0, named: null }))));
    expect(crop(db, ids[6])).toMatchObject({ state: 2, revision: 1 });
    expect(touched(db)).toEqual({ [ids[6]]: 3, [ids[7]]: 3 });
  });

  it('names no crop of a write that was refused', async () => {
    const { db, env, ids } = setup();
    db.exec(`INSERT INTO codes(field,code,key) VALUES('style',1,'regular')`);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}`, { ...local(ids[0], { revision: 7 }), id: uuid(6), verdict: 'match' })).status).toBe(409);
    expect((await send(env, `/atlas/characters/${publicId(ids[0])}/style`, { id: uuid(7), revision: 7, style: 'regular' })).status).toBe(409);
    expect(touched(db)).toEqual({});
  });
});

describe('the statements', () => {
  it('the routes ran read every table through an index', () => {
    expect(scans.size).toBeGreaterThan(20);
    expect([...scans].filter(([, found]) => found.length)).toEqual([]);
  });

  it('read every table through an index', () => {
    const { db, ids } = setup();
    const scans = (sql: string, ...args: unknown[]) => (db.query('EXPLAIN QUERY PLAN ' + sql).all(...(args as any[])) as { detail: string }[])
      .map(r => r.detail).filter(d => /^SCAN (?!j\b|json_each)/.test(d) && !d.includes('USING'));
    expect(scans(reviewCropsQuery(), JSON.stringify(ids))).toEqual([]);
    expect(scans(roundMembersQuery(), 'は')).toEqual([]);
    const env = { ATLAS: d1(db) } as unknown as Env;
    const changes = cropChanges(env, 1, 'u1:x', 1, [{ crop: ids[0], expected: 0, cut: 0, kind: 1, label: 'あ', state: 1, issue: 0, named: 'あ' }]) as any[];
    for (const statement of changes) expect(scans(statement.sql, ...statement.args)).toEqual([]);
  });

  it('name a user\'s actors by key for the ledger and by id for the review tables', () => {
    const db = new Database(':memory:'); databases.push(db);
    schema(db);
    db.exec(`INSERT INTO actors(id,key,user,via) VALUES(1,'u1','u1','account'),(2,'reviewer-0000000a','u1','legacy'),(3,'u2','u2','account')`);
    expect(db.query(`SELECT key FROM ${ownedKeys('u1')} ORDER BY 1`).all()).toEqual([{ key: 'reviewer-0000000a' }, { key: 'u1' }]);
    expect(db.query(`SELECT id FROM ${ownedActors('u1')} ORDER BY 1`).all()).toEqual([{ id: 1 }, { id: 2 }]);
  });
});

describe('a form claimed with its character', () => {
  const IVS = '\u{E0100}';
  const tools: ClaimTools = { fail: (status, message, extra): never => { throw new Problem(status, message, extra); }, body, text, canonical, literal, integer: () => 0 };
  function formed() {
    const { db, env, ids, page, objects } = setup();
    db.exec("INSERT INTO graphemes(id,text,head) VALUES(10,'図',10),(11,'圖',10)");
    db.exec(`UPDATE crops SET char=10 WHERE id=${ids[0]}; UPDATE crops SET char=NULL,family=10 WHERE id=${ids[6]}`);
    const version = (slot: number, box = `${10 * slot},0,10,10`) => slot >= 6 ? `${publicId(ids[slot])}@${G}@1,1,5,5` : `${publicId(ids[slot])}@${H}@${box}`;
    const save = (n: number, slot: number, more: object = {}, user = 'u1') => send(env, `/layers/units/${publicId(ids[slot])}`, { id: uuid(n), revision: 0,
      ...(slot >= 6 ? { source_revision: G } : { image_sha256: H }), verdict: 'wrong', issue: 'character', character: '圖', form: '⿴囗啚', crop_version: version(slot), ...more }, user);
    const pick = (n: number, slot: number, form: string | null, user = 'u1', seen = version(slot)) => setForm(env, { id: uuid(n), crop_version: seen, form }, publicId(ids[slot]), user, tools);
    const live = (slot: number) => db.query(`SELECT r.value AS form,a.asserted_by AS by,a.cut FROM assertions a JOIN forms f ON f.id=a.object JOIN representations r ON r.id=f.anchor WHERE a.crop=${ids[slot]}
      AND a.predicate='has_form' AND NOT EXISTS(SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.asserted_by,a.rowid`).all();
    const rows = () => ['submissions', 'events', 'reviews', 'assertions', 'assertion_actions', 'assertion_evidence', 'current_claims', 'ledger_submissions', 'forms', 'representations', 'carried_forms', 'tally']
      .map(table => [table, (db.query(`SELECT count(*) AS n FROM ${table}`).get() as any).n]);
    const shown = async (slot: number) => (await formOfCrop(env, ids[slot]))?.values.map((v: any) => v.text) ?? null;
    return { db, env, ids, page, objects, version, save, pick, live, rows, shown };
  }

  it('is saved in one submission, on a crop of another character and on one of none', async () => {
    const { db, ids, save, live, shown } = formed();
    for (const [n, slot] of [[1, 0], [2, 6]]) {
      expect((await save(n, slot)).status).toBe(200);
      expect(crop(db, ids[slot])).toMatchObject({ char: 11, state: 1, revision: 1 });
      expect([live(slot), await shown(slot)]).toEqual([[{ form: '⿴囗啚', by: 'u1', cut: 0 }], ['⿴囗啚']]);
      expect(db.query('SELECT count(*) AS n FROM assertions WHERE submission=? AND predicate=?').get(`u1:${uuid(n)}`, 'has_form')).toEqual({ n: 1 });
      expect(db.query('SELECT id,actor FROM ledger_submissions WHERE id=?').get(`u1:${uuid(n)}`)).toEqual({ id: `u1:${uuid(n)}`, actor: 'u1' });
    }
    // The card of the character it names lists it.
    expect(db.query('SELECT grapheme,count(*) AS n FROM carried_forms GROUP BY grapheme').all()).toEqual([{ grapheme: 11, n: 2 }]);
    // A figure and the character itself are forms of their character.
    expect((await save(3, 1, { form: '圖' + IVS })).status).toBe(200);
    expect((await save(4, 2, { form: '圖' })).status).toBe(200);
    expect([live(1), live(2)].flat().map((row: any) => row.form)).toEqual(['圖' + IVS, '圖']);
  });

  it('writes nothing where the form is none, another character’s, or not one crop’s', async () => {
    const { save, rows, env, ids } = formed(), before = rows();
    const refused = [{ form: '⿰木' }, { form: '図' + IVS }, { form: '図' }, { form: '' }, { form: null }, { form: ['圖'] }, { crop_version: undefined }, { character: undefined, issue: 'blank' }];
    for (const [i, more] of refused.entries()) expect([more, (await save(20 + i, 0, more)).status]).toEqual([more, 422]);
    expect((await send(env, '/atlas/rounds', { id: uuid(30), grapheme: 'U+56F3', answers: [local(ids[0], { verdict: 'wrong', issue: 'character', character: '圖', form: '圖', crop_version: 'x' })] })).status).toBe(422);
    expect((await send(env, '/atlas/corrections', { id: uuid(31), character: '圖', crops: [local(ids[0], { form: '圖', crop_version: 'x' })] })).status).toBe(422);
    expect(rows()).toEqual(before);
  });

  it('writes nothing on a crop that moved on, however late it moved', async () => {
    const { db, env, ids, save, rows, version } = formed(), before = rows();
    expect((await save(1, 0, { crop_version: version(0, '1,0,10,10') })).status).toBe(409);
    expect((await save(2, 0, { revision: 1 })).status).toBe(409);
    // Cut again between the read and the write: the review's own guard fails the claim with it.
    const batch = env.ATLAS.batch.bind(env.ATLAS);
    for (const change of ['cut=cut+1', 'revision=revision+5,cut=cut-1']) {
      let moved = false;
      const racing = { ...env, ATLAS: { ...env.ATLAS, batch: async (statements: any) => { if (!moved) { moved = true; db.exec(`UPDATE crops SET ${change} WHERE id=${ids[0]}`); } return batch(statements); } } } as Env;
      expect((await send(racing, `/layers/units/${publicId(ids[0])}`, { id: uuid(3), revision: 0, image_sha256: H, verdict: 'wrong', issue: 'character', character: '圖', form: '⿴囗啚', crop_version: version(0) })).status).toBe(409);
    }
    expect(rows()).toEqual(before);
  });

  it('rests on the cut its own new box makes, and its undo takes both back', async () => {
    const { db, env, ids, save, pick, live, shown } = formed(), box = { x: 2, y: 3, w: 8, h: 7 };
    await pick(9, 0, '図' + IVS);
    expect((await save(1, 0, { box })).status).toBe(200);
    expect([live(0), await shown(0)]).toEqual([[{ form: '⿴囗啚', by: 'u1', cut: 1 }], ['⿴囗啚']]);
    expect(db.query("SELECT e.ref,e.cut FROM assertion_evidence e JOIN assertions a ON a.id=e.assertion WHERE a.submission=? AND e.kind='crop'").all(`u1:${uuid(1)}`))
      .toEqual([{ ref: `${publicId(ids[0])}@${H}@2,3,8,7`, cut: 1 }]);
    expect((await send(env, `/atlas/corrections/${uuid(1)}/undo`, {})).status).toBe(200);
    expect([live(0), await shown(0), crop(db, ids[0])]).toEqual([[{ form: '図' + IVS, by: 'u1', cut: 0 }], ['図' + IVS], expect.objectContaining({ char: 10, state: 0, revision: 2 })]);
    expect(db.query('SELECT x FROM reviews WHERE crop=?').get(ids[0])).toEqual({ x: null });
  });

  it('answers a repeat with its first result, refuses its id with another form, and is claimed afresh after it was taken back', async () => {
    const { save, pick, shown, rows } = formed();
    const first = await save(1, 0, { form: '圖' + IVS }), saved = rows();
    expect(await save(1, 0, { form: '圖' + IVS })).toEqual(first);
    expect((await save(1, 0, { form: '⿴囗啚' })).status).toBe(409);
    expect(rows()).toEqual(saved);
    expect((await pick(2, 0, null)).form).toBeNull();
    expect((await pick(3, 0, '圖' + IVS)).form?.values).toMatchObject([{ text: '圖' + IVS }]);
    expect(await shown(0)).toEqual(['圖' + IVS]);
  });

  it('is undone with its review: the reader’s own form before it comes back, and no one else’s moves', async () => {
    const { db, env, save, pick, live, rows } = formed();
    await pick(10, 0, '図' + IVS);
    await pick(11, 0, '⿴囗斗', 'u2');
    await save(1, 0);
    expect(live(0)).toEqual([{ form: '⿴囗啚', by: 'u1', cut: 0 }, { form: '⿴囗斗', by: 'u2', cut: 0 }]);
    const named = rows().filter(([table]) => table === 'forms' || table === 'representations');
    expect((await send(env, `/atlas/corrections/${uuid(1)}/undo`, {})).status).toBe(200);
    expect(live(0)).toEqual([{ form: '図' + IVS, by: 'u1', cut: 0 }, { form: '⿴囗斗', by: 'u2', cut: 0 }]);
    // The names stay, which other crops may carry; a second undo does nothing more.
    expect(rows().filter(([table]) => table === 'forms' || table === 'representations')).toEqual(named);
    const after = rows();
    await send(env, `/atlas/corrections/${uuid(1)}/undo`, {});
    expect(rows()).toEqual(after);
    expect(db.query("SELECT count(*) AS n FROM assertion_evidence e JOIN assertions a ON a.id=e.assertion WHERE a.submission=? AND a.asserted_by='u1'").get(`u1:${uuid(10)}`)).toEqual({ n: 2 });
  });

  it('leaves a form its reader chose since, and one on a crop it never named', async () => {
    const { env, save, pick, live } = formed();
    await pick(10, 0, '図' + IVS);
    await save(1, 0);
    await pick(12, 0, '圖' + IVS);
    await pick(13, 1, '⿴囗斗');
    expect((await send(env, `/atlas/corrections/${uuid(1)}/undo`, {})).status).toBe(200);
    expect([live(0), live(1)]).toEqual([[{ form: '圖' + IVS, by: 'u1', cut: 0 }], [{ form: '⿴囗斗', by: 'u1', cut: 0 }]]);
    // Taken back by its reader before the undo: what it replaced stays replaced.
    await save(2, 2, { form: '圖' + IVS });
    await pick(14, 2, null);
    expect((await send(env, `/atlas/corrections/${uuid(2)}/undo`, {})).status).toBe(200);
    expect(live(2)).toEqual([]);
  });

  it('goes with a review an admin rejects, and stands where the undo is refused', async () => {
    const { db, env, ids, save, pick, live } = formed();
    await pick(10, 0, '図' + IVS);
    await save(1, 0);
    const own = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(1)}`) as Submission;
    await send(env, `/atlas/characters/${publicId(ids[0])}`, { id: uuid(2), revision: 1, image_sha256: H, verdict: 'match' }, 'u2');
    await expect(revert(env, own, own.actor, 'u1')).rejects.toThrow('later review');
    expect(live(0)).toEqual([{ form: '⿴囗啚', by: 'u1', cut: 0 }]);
    await save(3, 1, { form: '圖' });
    const other = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(3)}`) as Submission;
    await revert(env, other, own.actor, 'admin', { reason: 'r' });
    expect(live(1)).toEqual([]);
    expect(db.query("SELECT actor,reason FROM assertion_actions WHERE submission=?").all(`u1:${uuid(3)}:undo`)).toEqual([{ actor: 'admin', reason: 'undone' }]);
  });

  // Undone, a claim stands as it stood: whose it is, what was said of it and what it counts for.
  function judged() {
    const made = formed(), { db, env, ids } = made;
    const ledger = { fail: tools.fail, text, canonical, owned: ownedActors, alias: async () => null } as unknown as LedgerTools;
    const claim = (slot: number, by: string) => (db.query(`SELECT a.id FROM assertions a WHERE a.crop=${ids[slot]} AND a.predicate='has_form' AND a.asserted_by='${by}'
      AND NOT EXISTS(SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`).get() as { id: string }).id;
    const act = (n: number, slot: number, by: string, action: string, actor: string) => actOnClaim(env, { id: uuid(n), action }, claim(slot, by), actor, actor === 'admin', ledger);
    const undo = (n: number) => send(env, `/atlas/corrections/${uuid(n)}/undo`, {});
    const slot = async (n: number) => { const form = await formOfCrop(env, ids[n]); return [form?.status, (form?.values ?? []).map((v: any) => v.text)] };
    const ranks = () => (db.query(rankingQuery()).all() as { actor: string; total: number }[]).map(r => [r.actor, r.total]).sort();
    // Names 図 where 圖 stands, with a form of it.
    const again = async (n: number, slot: number) => expect((await made.save(n, slot, { revision: crop(db, ids[slot]).revision, character: '図', form: '図' + IVS })).status).toBe(200);
    const rejectAll = (input: object = { actor: 'u1' }) => reject(env, 'admin', input, { text } as any);
    return { ...made, act, undo, slot, ranks, again, rejectAll };
  }

  it('goes with every review of its reader an admin rejects, newest first, oldest first or after an undo', async () => {
    const twice = async () => {
      const made = judged();
      expect((await made.save(1, 0)).status).toBe(200);
      await made.again(2, 0);
      return made;
    };
    const newest = await twice();
    expect(await newest.rejectAll()).toMatchObject({ rejected: 2, conflicts: [] });
    expect([newest.live(0), crop(newest.db, newest.ids[0]).char]).toEqual([[], 10]);

    const oldest = await twice();
    expect(await oldest.rejectAll({ submissions: ['u1:' + uuid(1)] })).toMatchObject({ rejected: 0, conflicts: ['u1:' + uuid(1)] });
    expect(oldest.live(0)).toEqual([{ form: '図' + IVS, by: 'u1', cut: 0 }]);
    expect(await oldest.rejectAll()).toMatchObject({ rejected: 2 });
    expect(oldest.live(0)).toEqual([]);

    const undone = await twice();
    expect((await undone.undo(2)).status).toBe(200);
    expect(undone.live(0)).toEqual([{ form: '⿴囗啚', by: 'u1', cut: 0 }]);
    expect(await undone.rejectAll()).toMatchObject({ rejected: 1 });
    expect(undone.live(0)).toEqual([]);

    // What the first review replaced comes back through both.
    const picked = judged();
    await picked.pick(9, 0, '⿴囗斗');
    await picked.save(1, 0, { form: '圖' });
    await picked.again(2, 0);
    expect(await picked.rejectAll()).toMatchObject({ rejected: 2 });
    expect(picked.live(0)).toEqual([{ form: '⿴囗斗', by: 'u1', cut: 0 }]);
  });

  it('is undone in as many statements however often the form after it was claimed and undone', async () => {
    const statements = async (times: number) => {
      const made = judged();
      expect((await made.save(1, 0)).status).toBe(200);
      for (let n = 2; n < 2 + times; n++) { await made.again(n, 0); expect((await made.undo(n)).status).toBe(200); }
      let sent = 0;
      const counting = { ...made.env, ATLAS: { ...made.env.ATLAS, batch: (list: D1PreparedStatement[]) => { sent += list.length; return made.env.ATLAS.batch(list); } } } as Env;
      expect(await reject(counting, 'admin', { actor: 'u1' }, { text } as any)).toMatchObject({ rejected: 1 });
      expect(made.live(0)).toEqual([]);
      return sent;
    };
    expect(await statements(6)).toBe(await statements(0));
  });

  it('brings back a rejected form rejected, a disputed one decided and an accepted one accepted', async () => {
    const rejected = judged();
    await rejected.pick(10, 0, '図' + IVS);
    await rejected.act(11, 0, 'u1', 'reject', 'u2');
    const adjudicated = judged();
    await adjudicated.pick(10, 0, '図' + IVS);
    await adjudicated.pick(11, 0, '⿴囗斗', 'u2');
    await adjudicated.act(12, 0, 'u1', 'adjudicate', 'admin');
    const accepted = judged();
    await accepted.pick(10, 0, '図' + IVS);
    await accepted.act(11, 0, 'u1', 'reject', 'u2');
    await accepted.act(12, 0, 'u1', 'accept', 'u2');
    for (const [made, stands] of [[rejected, ['rejected', []]], [adjudicated, ['adjudicated', ['図' + IVS]]], [accepted, ['accepted', ['図' + IVS]]]] as const) {
      const before = [await made.slot(0), made.ranks()];
      expect(before[0]).toEqual(stands as any);
      // Twice over: a restored claim is restored again with what was said of it.
      for (const n of [1, 2]) {
        expect((await made.save(n, 0, { revision: crop(made.db, made.ids[0]).revision })).status).toBe(200);
        expect((await made.undo(n)).status).toBe(200);
        expect([await made.slot(0), made.ranks()]).toEqual(before);
      }
    }
    // What is said of the claim since counts with what was said of it before.
    const ranked = rejected.ranks();
    await rejected.act(20, 0, 'u1', 'accept', 'u2');
    expect([(await rejected.slot(0))[0], rejected.ranks()]).toEqual(['accepted', ranked]);
  });

  it('counts as it did once undone, whatever it replaced', async () => {
    const review = judged();
    await review.save(1, 0);
    // A form saved in a request of its own beside its review, as they once were.
    const apart = judged();
    await apart.save(1, 0, { form: undefined, crop_version: undefined });
    await setForm(apart.env, { id: uuid(9), crop_version: apart.version(0), form: '⿴囗斗', review: uuid(1) }, publicId(apart.ids[0]), 'u1', tools);
    const moved = judged();
    await moved.save(1, 0, { form: undefined, crop_version: undefined });
    await moved.pick(9, 0, '図' + IVS);
    moved.db.exec(`INSERT INTO written_forms(id,submission,crop,actor,revision,pixels,label,form,request,at) VALUES('w1','u1:w',${moved.ids[0]},'u1',1,'p','図','図','{}',1);
      UPDATE assertions SET legacy='written_forms:w1' WHERE predicate='has_form'`);
    const alone = judged();
    await alone.save(1, 0, { form: undefined, crop_version: undefined });
    await alone.pick(9, 0, '図' + IVS);
    for (const made of [review, apart, moved, alone]) {
      const before = [made.ranks(), made.live(0)];
      await made.again(2, 0);
      expect((await made.undo(2)).status).toBe(200);
      expect([made.ranks(), made.live(0)]).toEqual(before);
    }
    expect([review, apart, moved, alone].map(made => made.ranks())).toEqual([[['u1', 1]], [['u1', 1]], [['u1', 2]], [['u1', 2]]]);
  });

  it('leaves the form of a crop whose review claims none', async () => {
    const { save, pick, live } = formed();
    await pick(10, 0, '⿴囗啚');
    expect((await save(1, 0, { form: undefined, crop_version: undefined })).status).toBe(200);
    expect(live(0)).toEqual([{ form: '⿴囗啚', by: 'u1', cut: 0 }]);
  });

  // The resolver scans its own list of slots.
  it('seeks every row it reads or undoes', async () => {
    const { save, env, pick } = formed();
    scans.clear();
    await pick(10, 0, '図' + IVS);
    await save(1, 0);
    await send(env, `/atlas/corrections/${uuid(1)}/undo`, {});
    expect([...scans].filter(([sql, plan]) => plan.length && /assertion|ledger_submissions|current_claims/.test(sql) && !/^INSERT INTO current_claims/.test(sql)).map(([sql, plan]) => [sql.slice(0, 80), plan])).toEqual([]);
  });
});
