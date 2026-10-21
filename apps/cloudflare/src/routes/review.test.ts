import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { ownedActors, ownedKeys } from '../auth';
import { Problem } from '../busy';
import { body, canonical, literal, text } from '../input';
import { setForm, type ClaimTools } from './claims';
import { STALE_CODE } from '../atlas3Crops';
import { cropChanges, reviewCrops, reviewCropsQuery, reviewRoute, revert, roundMembersQuery, type Submission } from './review';

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

let pages = 7000;
// One page: local crops 0–5 (は, ハ, は, は, あ, あ), corpus glyphs 6–8 (は), all pending and dealt.
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  schema(db);
  const page = ++pages, ids = Array.from({ length: 9 }, (_, slot) => cropId(page, slot));
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
    box: { x: 10 * slot, y: 0, w: 10, h: 10 }, context: true, context_box: { x: 0, y: 0, w: 200, h: 200 }, crop_box: { x: 10 * slot, y: 0, w: 10, h: 10 },
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
  const MEDIA = { get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    const bytes = objects.get(name);
    if (!bytes) return null;
    const part = options?.range ? bytes.subarray(options.range.offset, options.range.offset + options.range.length) : bytes;
    return { body: new Response(part).body!, arrayBuffer: async () => new Uint8Array(part).buffer };
  } };
  const env = { ATLAS: planned(db), MEDIA, CORRECTIONS: { limit: async () => ({ success: true }) } } as unknown as Env;
  return { db, env, ids, page };
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

  it('refuses a round whose grapheme is a member of another, and a crop of another grapheme', async () => {
    const { env, ids } = setup();
    expect((await send(env, '/atlas/rounds', { id: uuid(1), grapheme: 'U+30CF', seen: [local(ids[1])] })).body.detail).toBe('ハ is filed under は.');
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

  it.each(['batch', 'corpus'])('keeps the %s response when a mark forces a retry', async mode => {
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
    const path = mode === 'batch' ? '/atlas/corrections' : '/atlas/corpus/reviews';
    const input = mode === 'batch' ? { id: uuid(1), character: 'は', crops: [local(id)] }
      : { id: uuid(1), identity: publicId(id), revision: 0, source_revision: G, verdict: 'match' };
    const saved = await send(env, path, input);
    expect(saved.status).toBe(200);
    if (mode === 'batch') expect(saved.body).toMatchObject({ id: uuid(1), unchanged: [], results: [{ target_id: publicId(id), state: 'checked' }] });
    else expect(saved.body).toMatchObject({ id: publicId(id), origin: 'corpus', event: { state: 'checked', revision: 2 } });
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
    const rejected = await revert(env, own, actor('u1'), { by: 'admin', reason: 'r', reviewer: [actor('u1')] });
    expect(rejected.results).toHaveLength(1);
    expect(db.query('SELECT by,reason FROM rejections').all()).toEqual([{ by: 'admin', reason: 'r' }]);
    expect(crop(db, ids[0])).toMatchObject({ char: 1, state: 0, revision: 4 });
    expect(db.query('SELECT undone FROM submissions WHERE key=?').get(`u1:${uuid(1)}`)).toEqual({ undone: 1 });
    // Anyone else's later change stops it, even undone, as before.
    expect((await review(3, 4, { verdict: 'unsure' }, 'u1')).status).toBe(200);
    expect((await review(4, 5, { verdict: 'match' }, 'u2')).status).toBe(200);
    expect((await send(env, `/atlas/corrections/${uuid(4)}/undo`, {}, 'u2')).status).toBe(200);
    const third = db.query('SELECT id,key,actor,at,undone FROM submissions WHERE key=?').get(`u1:${uuid(3)}`) as Submission;
    await expect(revert(env, third, actor('u2'), { by: 'admin', reason: 'r', reviewer: [actor('u1')] })).rejects.toThrow('later review');
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
  it('keeps a saved form in the review response and its submission retry', async () => {
    const { db, env, ids } = setup();
    const tools: ClaimTools = { body, canonical: value => canonical(value), literal, text,
      fail: (status, message, extra) => { throw new Problem(status, message, extra); },
      integer: () => { throw new Error('No pagination in a form write.'); } };
    // Claims have their own query-plan checks; this suite tracks the review response's reads.
    const picked = await setForm({ ...env, ATLAS: d1(db) }, { id: uuid(10), form: '𛂞', crop_version: `${publicId(ids[6])}@${G}@1,1,5,5` },
      publicId(ids[6]), 'u1', tools);
    expect(picked.form?.values[0].text).toBe('𛂞');
    const input = { id: uuid(11), identity: publicId(ids[6]), revision: 0, source_revision: G, verdict: 'match' };
    for (let attempt = 0; attempt < 2; attempt++) {
      const reviewed = await send(env, '/atlas/corpus/reviews', input);
      expect(reviewed.status).toBe(200);
      expect(reviewed.body.form).toEqual(picked.form);
    }
  });

  it('answers with the glyph as listings show it, and numbers corpus glyphs in the order the old site wrote their rows', async () => {
    const { db, env, ids } = setup();
    const glyph = (id: number, extra = {}) => ({ id: publicId(id), revision: 0, source_revision: G, ...extra });
    const reviewed = await send(env, '/atlas/corpus/reviews', { id: uuid(1), identity: publicId(ids[6]), revision: 0, source_revision: G,
      verdict: 'wrong', issue: 'character', character: 'ハ' });
    expect(reviewed.status).toBe(200);
    expect(reviewed.body).toMatchObject({ id: publicId(ids[6]), origin: 'corpus', label: 'ハ', state: 'checked', revision: 1,
      written_character: 'ハ', identity_status: 'assigned', grapheme: 'U+306F', visual_group: null, form: null, event: { revision: 1, state: 'checked' } });
    await send(env, '/atlas/rounds', { id: uuid(2), grapheme: 'U+306F', answers: [glyph(ids[8], { verdict: 'unsure' })],
      seen: [glyph(ids[7])], skipped: [glyph(ids[6], { revision: 1 })] });
    expect(db.query('SELECT crop FROM corpus_rows ORDER BY seq').all().map((r: any) => r.crop)).toEqual([ids[6], ids[8], ids[7]]);
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
