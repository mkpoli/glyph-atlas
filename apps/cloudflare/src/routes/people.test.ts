// The people routes on ATLAS and ACCOUNTS: favourites, the history, the ranking and the admin lists.
// Each is checked against the route as origin/main answered it (fixtures/*.old.ts), on one set of
// reviews built into the old schema and into the new one, the answers compared after mapping the ids.
import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, cropItems, publicId } from '../atlas3';
import { StaleCrop } from '../atlas3Crops';
import { canonical } from '../index';
import { Problem } from '../busy';
import { bookDatingQuery } from '../dating';
import { FAVOURITES_HELD, FavouriteError, favouriteCountQuery, favouriteCrops, favouriteIds, favouriteIdsQuery, favouritePageQuery,
  setFavourite } from './favourites';
import { decodeCursor, encodeCursor, history, historyQuery } from './history';
import { rankingQuery, rankingRows } from './ranking';
import { activeUsersQuery, flagRequest, flagRowsQuery, flaggedCorpus, flaggedCorpusQuery, rejectNamedQuery, rejectReviewerQuery,
  reviewerActors, reviewers, submissions, submissionsQuery, summary, unclaimedQuery, userWorkQuery } from './admin';
import * as old from './fixtures/history.old';
import { rankingQuery as oldRankingQuery, rankingRow as oldRankingRow } from './fixtures/ranking.old';
import { reviewers as oldReviewers, submissions as oldSubmissions } from './fixtures/admin.old';
import { favouriteCrops as oldFavouriteCrops, favouriteIds as oldFavouriteIds } from './fixtures/favourites.old';

type Json = Record<string, any>;
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function migrated(dir: string) {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL(`../../${dir}/`, import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
// What the review port's migration 0009 adds and these routes read, until it lands beside 0010.
function withUndo(db: Database) {
  if (!(db.query(`SELECT 1 FROM pragma_table_info('events') WHERE name='reverses'`).get()))
    db.exec(`ALTER TABLE events ADD COLUMN reverses INTEGER; INSERT INTO codes(field,code,key) VALUES('kind',5,'undo');`);
  return db;
}
const tools = {
  text: (value: unknown, max: number, name: string, required = false) => {
    if (value == null && !required) return null;
    if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw new Problem(422, `Invalid ${name}.`);
    return value.trim();
  },
  integer: (q: URLSearchParams, key: string, fallback: number, max = 1000000) => {
    const n = Number(q.get(key) ?? fallback);
    if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
    return n;
  },
};
const plan = (db: Database, sql: string, values: unknown[] = []) =>
  (db.query('EXPLAIN QUERY PLAN ' + sql).all(...(values as any[])) as { detail: string }[]).map(r => r.detail);
// A full read of a table that grows with the site: never in a request.
const scans = (details: string[], tables: string[]) => details.filter(d => tables.some(t => new RegExp(`^SCAN ${t}\\b`).test(d) && !/COVERING INDEX|USING INDEX/.test(d)));

// One set of people, crops and reviews, written into both schemas.
const minute = (m: number) => `2026-10-01T00:${String(m).padStart(2, '0')}:00.000Z`;
const ms = (m: number) => Date.parse(minute(m));
// Pages no other test file takes: bundles are held per isolate.
const P = 870001, Q = 870002;
const crops = { a: { text: 'hk:a', id: cropId(P, 0), label: 'あ', book: 1 }, b: { text: 'hk:b', id: cropId(P, 1), label: 'い', book: 1 },
  c: { text: 'hk:c', id: cropId(Q, 0), label: 'い', book: 2 } };
type CropName = keyof typeof crops;
const users = [
  { id: 'u1', name: 'Ada', email: 'ada@x', image: '/a.png', anonymous: 0, created: minute(0), role: 'admin' },
  { id: 'u2', name: 'anon-00aa11', email: 'b@anonymous.invalid', image: null, anonymous: 1, created: minute(1), role: null },
  { id: 'u3', name: 'Bea', email: 'bea@x', image: null, anonymous: 0, created: minute(2), role: null },
];
const actors = [{ key: 'u1', user: 'u1', via: 'account' }, { key: 'reviewer-0000000a', user: 'u1', via: 'legacy' },
  { key: 'u2', user: 'u2', via: 'account' }, { key: 'reviewer-0000000f', user: null, via: null }];
const graphemes = ['あ', 'い', 'ア', 'プ', '様'];
// Submissions: a round names its label, a batch its character; `seen` are the crops a round passed.
type Sub = { key: string; actor: string; m: number; target: string | null; input: Json; undone?: number; seen?: CropName[] };
const subs: Sub[] = [
  { key: 'reviewer-0000000a:r1', actor: 'reviewer-0000000a', m: 1, target: null, input: { id: 'r1', label: 'あ' }, seen: ['b'] },
  { key: 'u1:s1', actor: 'u1', m: 2, target: 'hk:b', input: { id: 's1', verdict: 'wrong', issue: 'character', character: 'い' } },
  { key: 'u2:b1', actor: 'u2', m: 3, target: '@batch', input: { id: 'b1', character: 'ア', crops: [{}] }, undone: 1 },
  { key: 'reviewer-0000000f:s2', actor: 'reviewer-0000000f', m: 4, target: 'hk:c', input: { id: 's2', verdict: 'wrong', issue: 'reading' } },
  { key: 'u1:s3', actor: 'u1', m: 5, target: 'hk:b', input: { id: 's3', verdict: 'wrong', issue: 'character', character: 'い' } },
  { key: 'u1:r2', actor: 'u1', m: 6, target: null, input: { id: 'r2', label: 'い' }, seen: ['a', 'b'] },
  { key: 'u1:r3', actor: 'u1', m: 7, target: null, input: { id: 'r3', label: 'あ' }, seen: ['a'], undone: 1 },
];
// Events, in id order: the crop, the submission, the verdict and issue, the label shown and set, the
// character named and the characters typed. `undoes` makes an undo of that event.
type Ev = { crop: CropName; sub: string; actor: string; m: number; verdict?: string; issue?: string | null; shown: string; after: string;
  named?: string | null; text?: string | null; undoes?: number };
const events: Ev[] = [
  { crop: 'a', sub: 'reviewer-0000000a:r1', actor: 'reviewer-0000000a', m: 1, verdict: 'match', shown: 'あ', after: 'あ' },
  { crop: 'b', sub: 'u1:s1', actor: 'u1', m: 2, verdict: 'wrong', issue: 'character', shown: 'あ', after: 'い', named: 'い' },
  { crop: 'a', sub: 'u2:b1', actor: 'u2', m: 3, verdict: 'wrong', issue: 'character', shown: 'あ', after: 'ア', named: 'ア' },
  { crop: 'c', sub: 'reviewer-0000000f:s2', actor: 'reviewer-0000000f', m: 4, verdict: 'wrong', issue: 'reading', shown: 'い', after: 'い', text: 'ハツ' },
  { crop: 'b', sub: 'u1:s3', actor: 'u1', m: 5, verdict: 'wrong', issue: 'character', shown: 'い', after: 'い', named: 'い' },
  { crop: 'c', sub: 'u1:r2', actor: 'u1', m: 6, verdict: 'unsure', shown: 'い', after: 'い' },
  { crop: 'a', sub: 'u2:b1', actor: 'u2', m: 8, shown: 'あ', after: 'あ', undoes: 3 },
];
const cp = (c: string) => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0');
const dating = { kind: 'copied', start: 1700, end: 1710, precision: 'decade', qualifier: null, uncertain: 0, text: '宝永', label: '1700s',
  status: 'single', calendar: 'gregorian', conversion: '{"query":"q"}', source: 'src:1' };

function oldDatabase() {
  const db = migrated('migrations');
  // The rows stand alone here, without the crops a review applies to.
  db.exec('DROP TRIGGER event_revision_guard; DROP TRIGGER event_apply; PRAGMA foreign_keys=OFF;');
  const user = db.prepare(`INSERT INTO "user"(id,name,email,emailVerified,image,createdAt,updatedAt,isAnonymous,role) VALUES(?,?,?,1,?,?,?,?,?)`);
  for (const u of users) user.run(u.id, u.name, u.email, u.image, u.created, u.created, u.anonymous, u.role);
  for (const a of actors) if (a.user) db.prepare('INSERT INTO actors VALUES(?,?,?,?)').run(a.key, a.user, a.via, minute(0));
  const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document)
    VALUES(?,'local',?,'handwritten','kana','pending',0,1,1,0,'{}','{}','{}','{}',?)`);
  for (const c of Object.values(crops)) unit.run(c.text, c.label, `book${c.book}`);
  db.prepare(`INSERT INTO document_dating(document,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,claims,calendar,conversion,source,resolver,export)
    VALUES('book1','witness',?,?,?,?,?,?,?,?,?,'[]',?,?,?,'r','e')`).run(dating.kind, dating.start, dating.end, dating.precision, dating.qualifier,
    dating.uncertain, dating.text, dating.label, dating.status, dating.calendar, dating.conversion, dating.source);
  for (const s of subs) {
    db.prepare('INSERT INTO submissions(id,actor,request,response,at,undone) VALUES(?,?,?,?,?,?)')
      .run(s.key, s.actor, canonical({ target: s.target, input: s.input }), '{}', minute(s.m), s.undone ?? 0);
    for (const c of s.seen ?? []) db.prepare(`INSERT INTO seen(target,submission,box,image_sha256,at) VALUES(?,?,NULL,'h',?)`).run(crops[c].text, s.key, minute(s.m));
  }
  events.forEach((e, i) => {
    const s = subs.find(x => x.key === e.sub)!, snapshot = { character: { label: e.shown } };
    const evidence = e.undoes ? `undo of cf:e${e.undoes}` : JSON.stringify({ kind: s.target === null ? 'visual-quiz' : 'character-review',
      ...(s.target === null ? { round: s.input.id, label: e.shown } : s.target === '@batch' ? { batch: s.input.id } : { request: s.input }),
      verdict: e.verdict, issue: e.issue ?? null, note: '', suggested_character: e.named ? cp(e.named) : null,
      ...(e.issue === 'reading' ? { suggested_reading: e.text } : { suggested_text: e.text ?? null }), snapshot });
    db.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES(?,?,?,?,0,?,?,?,?,?,?)`)
      .run(`cf:e${i + 1}`, e.sub, crops[e.crop].text, e.actor, JSON.stringify({ label: e.shown }), JSON.stringify({ label: e.after }),
        JSON.stringify({ id: `cf:e${i + 1}`, evidence }), JSON.stringify(snapshot), e.undoes ? 'undo' : 'review', minute(e.m));
  });
  db.prepare(`INSERT INTO rejections(submission,by,reason,at) VALUES('u2:b1','u3','spam',?)`).run(minute(9));
  return db;
}

function newDatabases() {
  const atlas = withUndo(migrated('migrations-atlas')), accounts = migrated('migrations-accounts');
  const user = accounts.prepare(`INSERT INTO "user"(id,name,email,emailVerified,image,createdAt,updatedAt,isAnonymous,role) VALUES(?,?,?,1,?,?,?,?,?)`);
  for (const u of users) user.run(u.id, u.name, u.email, u.image, u.created, u.created, u.anonymous, u.role);
  const actorId = new Map(actors.map((a, i) => [a.key, i + 1]));
  for (const a of actors) atlas.prepare('INSERT INTO actors(id,key,user,via,at) VALUES(?,?,?,?,?)').run(actorId.get(a.key)!, a.key, a.user, a.via, a.user ? ms(0) : null);
  atlas.exec(`INSERT INTO books(id,key) VALUES(1,'book1'),(2,'book2');
    INSERT INTO pages(id,key,book,rev) VALUES(${P},'hk:p1',1,1),(${Q},'hk:p2',2,1);`);
  graphemes.forEach((g, i) => atlas.prepare('INSERT INTO graphemes(id,text) VALUES(?,?)').run(i + 1, g));
  const gid = (g: string | null | undefined) => g ? graphemes.indexOf(g) + 1 : null;
  for (const c of Object.values(crops)) atlas.prepare('INSERT INTO crops(id,kind,book,char,state,quiz,shuffle) VALUES(?,0,?,?,0,1,0)').run(c.id, c.book, gid(c.label));
  atlas.prepare(`INSERT INTO book_dates(book,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source)
    VALUES(1,'witness',?,?,?,?,?,?,?,?,?,?,?,?)`).run(dating.kind, dating.start, dating.end, dating.precision, dating.qualifier, dating.uncertain,
    dating.text, dating.label, dating.status, dating.calendar, dating.conversion, dating.source);
  const subId = new Map(subs.map((s, i) => [s.key, i + 1]));
  for (const s of subs) {
    atlas.prepare('INSERT INTO submissions(id,key,actor,request,response,at,undone) VALUES(?,?,?,?,?,?,?)')
      .run(subId.get(s.key)!, s.key, actorId.get(s.actor)!, canonical({ target: s.target, input: s.input }), '{}', ms(s.m), s.undone ?? 0);
    for (const c of s.seen ?? []) atlas.prepare('INSERT INTO seen(submission,crop,cut,at) VALUES(?,?,0,?)').run(subId.get(s.key)!, crops[c].id, ms(s.m));
  }
  const kinds: Record<string, number> = { match: 0, wrong: 1, unsure: 2 }, issues: Record<string, number> = { character: 0, reading: 6 };
  const revision = new Map<number, number>();
  events.forEach((e, i) => {
    const crop = crops[e.crop].id, rev = (revision.get(crop) ?? 0) + 1;
    revision.set(crop, rev);
    atlas.prepare(`INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state,issue,text,shown,named,reverses)
      VALUES(?,?,?,0,0,?,?,?,?,?,1,?,?,?,?,?)`).run(i + 1, crop, rev, actorId.get(e.actor)!, subId.get(e.sub)!, ms(e.m),
      e.undoes ? 5 : kinds[e.verdict!], gid(e.after), e.undoes || !e.issue ? null : issues[e.issue], e.undoes ? null : e.text ?? null,
      gid(e.shown), e.undoes ? null : gid(e.named), e.undoes ?? null);
  });
  atlas.prepare(`INSERT INTO rejections(submission,by,reason,at) VALUES(?,'u3','spam',?)`).run(subId.get('u2:b1')!, ms(9));
  // Each actor's totals, as history.py counts them from the moved history.
  atlas.exec(`INSERT INTO actor_stats SELECT a.id,(SELECT count(*) FROM events e JOIN submissions s ON s.id=e.submission
    WHERE e.actor=a.id AND s.undone=0 AND e.kind IN (0,1,2)),(SELECT count(*) FROM submissions s WHERE s.actor=a.id AND s.undone=0),
    (SELECT max(at) FROM submissions s WHERE s.actor=a.id) FROM actors a`);
  return { atlas, accounts, actorId, subId };
}

// Bundles for the crops, so listing items and retired crops read as they do in R2.
function media(entries: Record<number, Json[]>) {
  const objects = new Map<string, Uint8Array>(Object.entries(entries).map(([page, list]) =>
    [bundleKey(Number(page), 1), gzipSync(JSON.stringify({ key: `hk:p${page}`, book: 1, rev: 1, page: null, crops: list }))]));
  return { get: async (name: string) => {
    const bytes = objects.get(name);
    return bytes ? { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null;
  } };
}
function setup() {
  const oldDb = oldDatabase(), fresh = newDatabases();
  const MEDIA = media({ [P]: [{ slot: 0, id: 'hk:a' }, { slot: 1, id: 'hk:b' }, { slot: 2, id: 'hk:gone', retired: true, replaced_by: crops.b.id }],
    [Q]: [{ slot: 0, id: 'hk:c' }] });
  return { oldDb, ...fresh, oldEnv: { DB: d1(oldDb) } as unknown as Env,
    env: { ATLAS: d1(fresh.atlas), ACCOUNTS: d1(fresh.accounts), MEDIA } as unknown as Env };
}

// The old answer with its ids as the new layout writes them: crops by public id, events by number.
const textToPublic = new Map(Object.values(crops).map(c => [c.text, publicId(c.id)]));
const eventId = (id: string | null) => id === null ? null : id.replace(/^cf:e/, '');
function mappedHistory(item: Json): Json {
  return { ...item, id: item.kind === 'passed' ? item.id : eventId(item.id), target: item.target === null ? null : textToPublic.get(item.target),
    undoes: eventId(item.undoes) };
}
async function pages(read: (before: string | null) => Promise<{ items: Json[]; next: string | null }>) {
  const out: Json[][] = [];
  let before: string | null = null;
  do { const page = await read(before); out.push(page.items); before = page.next; } while (before);
  return out;
}

describe('the history', () => {
  const queries = [{}, { user: 'u1' }, { mine: 'true' }, { label: 'あ' }, { label: 'い', user: 'u1' }, { label: 'ん' }, { user: 'u3' },
    { user: 'nobody' }];
  for (const params of queries) it(`answers ${JSON.stringify(params)} as before, page for page`, async () => {
    const { oldEnv, env } = setup();
    for (const limit of ['40', '2']) {
      const before = await pages(b => old.history(oldEnv, new URLSearchParams({ ...params, limit, ...(b ? { before: b } : {}) }), 'u1'));
      const after = await pages(b => history(env, new URLSearchParams({ ...params, limit, ...(b ? { before: b } : {}) }), 'u1', tools));
      expect(after).toEqual(before.map(page => page.map(mappedHistory)));
    }
  });

  it('names reviewers, dates their books, and maps each kind of row', async () => {
    const { env } = setup();
    const { items } = await history(env, new URLSearchParams(), 'u1', tools);
    expect(items.map((i: Json) => [i.kind, i.id, i.reviewer.name, i.reviewer.mine])).toEqual([
      ['undo', '7', 'anon-00aa11', false], ['passed', 'u1:r2', 'Ada', true], ['review', '6', 'Ada', true], ['review', '5', 'Ada', true],
      ['review', '4', 'reviewer-0000000f', false], ['review', '3', 'anon-00aa11', false], ['review', '2', 'Ada', true],
      ['passed', 'reviewer-0000000a:r1', 'Ada', true], ['review', '1', 'Ada', true]]);
    const byId = new Map(items.map((i: Json) => [i.id, i]));
    expect(byId.get('7')).toMatchObject({ undoes: '3', label: 'あ', verdict: null, round: null, batch: null });
    expect(byId.get('3')).toMatchObject({ batch: 'b1', round: null, character: 'ア', issue: 'character' });
    expect(byId.get('4')).toMatchObject({ issue: 'character', text: 'ハツ', dating: {} });
    expect(byId.get('5')).toMatchObject({ character: 'い', label: 'い' });
    expect(byId.get('1')).toMatchObject({ round: 'r1', target: publicId(crops.a.id) });
    expect(byId.get('1').dating.witness).toMatchObject({ start: 1700, hutime: 'q' });
    expect(byId.get('u1:r2')).toMatchObject({ passed: 2, label: 'い', round: 'u1:r2', target: null, dating: {} });
  });

  it('reads each page by index, never the whole journal', () => {
    const { atlas } = setup();
    for (const [actors, label, cursor] of [[null, null, null], [[1, 2], null, { at: 5, src: 0, id: 3 }], [null, { text: 'あ', shown: 1 }, null],
      [[1], { text: 'い', shown: 2 }, { at: 5, src: 1, id: 3 }]] as const) {
      const { sql, values } = historyQuery(actors as number[] | null, label, cursor);
      const details = plan(atlas, sql, [...values, 41]);
      expect(scans(details, ['events', 'submissions', 'seen', 'graphemes', 'pages'])).toEqual([]);
      if (actors) expect(details.some(d => /SEARCH events USING INDEX event_actor_history/.test(d) || /SEARCH events USING INDEX event_shown/.test(d))).toBe(true);
      if (label && !actors) expect(details.some(d => /event_shown/.test(d))).toBe(true);
      if (label && !actors) expect(details.some(d => /submission_label/.test(d))).toBe(true);
    }
    expect(scans(plan(atlas, bookDatingQuery(), ['[1]']), ['book_dates'])).toEqual([]);
  });

  it('reads a user holding more ids than one statement takes in groups, merged into one order, ties by id', async () => {
    const { atlas, env } = setup();
    const held = Array.from({ length: 7 }, (_, i) => 10 + i);
    for (const id of held) atlas.prepare(`INSERT INTO actors(id,key,user,via,at) VALUES(?,?,'u3','legacy',0)`).run(id, `reviewer-${id}`);
    atlas.prepare(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(100,'u3:x',10,'{"target":"hk:a","input":{"id":"x"}}','{}',0)`).run();
    // Ids in later groups wrote the newest rows, and two rows tie across groups.
    const minutes = [3, 10, 20, 7, 30, 20, 1];
    held.forEach((actor, i) => atlas.prepare(`INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,state,shown)
      VALUES(?,?,?,0,0,?,100,?,0,1,1)`).run(200 + i, crops.a.id, 100 + i, actor, ms(minutes[i])));
    const want = held.map((_, i) => i).sort((a, b) => minutes[b] - minutes[a] || b - a).map(i => String(200 + i));
    const read = await pages(b => history(env, new URLSearchParams({ user: 'u3', limit: '3', ...(b ? { before: b } : {}) }), null, tools));
    expect(read.map(p => p.length)).toEqual([3, 3, 1]);
    expect(read.flat().map(i => i.id)).toEqual(want);
  });

  it('keeps a cursor to its three numbers', () => {
    expect(decodeCursor(encodeCursor({ at: 5, src: 1, id: 9 }))).toEqual({ at: 5, src: 1, id: 9 });
    for (const bad of ['not-base64!!', btoa('[1,2]'), btoa('[1,2,3]'), btoa('["1",0,3]')]) expect(() => decodeCursor(bad)).toThrow('Invalid cursor.');
  });

  it('lists the undo of a review and leaves out a mark and the undo that clears it', async () => {
    const { atlas, env } = setup();
    // An undone round (#812's shape): its verdict, the seen mark it set on a crop it showed, and on undo
    // one event reversing each, all under the round's submission; a mark cleared with no event to name.
    const round = 6;
    atlas.exec(`UPDATE submissions SET undone=1 WHERE id=${round};
      INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state,shown) VALUES
        (20,${crops.a.id},10,0,0,1,${round},${ms(6)},3,1,3,1),
        (21,${crops.a.id},11,0,0,1,${round},${ms(10)},5,1,0,1),
        (22,${crops.c.id},10,0,0,1,${round},${ms(10)},5,2,0,2),
        (23,${crops.b.id},10,0,0,1,${round},${ms(10)},5,2,0,2);
      UPDATE events SET reverses=20 WHERE id=21; UPDATE events SET reverses=6 WHERE id=22;`);
    const { items } = await history(env, new URLSearchParams({ user: 'u1', limit: '3' }), 'u1', tools);
    expect(items.map((i: Json) => [i.id, i.kind, i.undoes])).toEqual([['22', 'undo', '6'], ['6', 'review', null], ['5', 'review', null]]);
  });
});

describe('the ranking', () => {
  function ledger(db: Database, fresh: boolean) {
    // u2 claims a form, then another, which retracts the first; renames a crop by a review its claim
    // names; u1 claims one under its legacy id and u2 accepts it twice; a source's and a model's claims
    // are nobody's, and so is the one moved from a written form.
    const at = fresh ? ms(1) : `'${minute(1)}'`;
    db.exec(`INSERT INTO written_forms(id,submission,${fresh ? 'crop' : 'target'},actor,revision,pixels,label,form,request,at)
        VALUES('w1','u2:w',${fresh ? 1 : "'t'"},'u2',1,'p','あ','あ','{}',${at});
      INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note${fresh ? ',crops' : ''})
        VALUES('d1','${minute(1)}','reviewer-0000000a','cluster','U+3042','𛀂','c','r','[]',''${fresh ? ",'[]'" : ''});
      INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES
        ('u2:f1','u2','{"input":{"form":"x"}}','{}','${minute(1)}'),('u2:f2','u2','{"input":{"form":"y"}}','{}','${minute(1)}'),
        ('u2:f3','u2','{"input":{"form":"z","review":"rename"}}','{}','${minute(1)}'),('u1:c','u1','{}','{}','${minute(1)}');`);
    const claim = db.prepare(`INSERT INTO assertions(id,submission,subject,predicate,object,value,tier,asserted_by,asserted_at,run,legacy)
      VALUES(?,?,?,?,?,?,?,?,'${minute(1)}',?,?)`);
    claim.run('a1', 'u2:f1', 't', 'has_form', 'fm:1', null, 'observed', 'u2', null, null);
    claim.run('a1n', 'u2:f1', 'fm:1', 'represented_by', 'rp:1', null, 'observed', 'u2', null, null);
    claim.run('a2', 'u2:f2', 't', 'has_form', 'fm:2', null, 'observed', 'u2', null, null);
    claim.run('a3', 'u2:f3', 't2', 'has_form', 'fm:3', null, 'observed', 'u2', null, null);
    claim.run('a6', 'u1:c', 't4', 'has_form', null, '"unreadable"', 'observed', 'reviewer-0000000a', null, null);
    claim.run('a7', null, 'doc', 'date_written', null, '{}', 'attested', 'source:x', null, null);
    claim.run('a8', null, 't5', 'has_form', 'fm:1', null, 'derived', 'model', 'run-1', null);
    claim.run('a9', 'u2:w', 't6', 'has_form', 'fm:1', null, 'observed', 'u2', null, 'written_forms:w1');
    db.exec(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at) VALUES
      ('x1','u2:f2','a1','retract','u2','${minute(1)}'),('x2','u2:ok','a6','accept','u2','${minute(1)}'),('x3','u2:ok2','a6','accept','u2','${minute(1)}');`);
  }
  it('ranks as before, names from ACCOUNTS', async () => {
    const { oldDb, atlas, env, subId } = setup();
    ledger(oldDb, false);
    ledger(atlas, true);
    // The renaming review the claim names, standing.
    oldDb.exec(`INSERT INTO submissions(id,actor,request,response,at) VALUES('u2:rename','u2','{}','{}','${minute(1)}')`);
    atlas.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(${subId.size + 1},'u2:rename',3,'{}','{}',${ms(1)})`);
    const before = oldDb.query(oldRankingQuery()).all().map((row, i) => oldRankingRow(row as Json, i + 1));
    const after = await rankingRows(env);
    expect(after).toEqual(before);
    expect(after.map(r => [r.name, r.total])).toEqual([['Ada', 6], ['anon-00aa11', 3], ['reviewer-0000000f', 1]]);
  });
  it('reads each table by index, the reviews from the running totals', () => {
    const { atlas } = setup();
    const details = plan(atlas, rankingQuery());
    expect(scans(details, ['events', 'submissions', 'assertions', 'assertion_actions', 'ledger_submissions', 'actors'])).toEqual([]);
    expect(details.some(d => d.includes('assertion_person'))).toBe(true);
    expect(details.some(d => /^SCAN events/.test(d))).toBe(false);
  });
});

describe('favourites', () => {
  function starred(oldDb: Database, atlas: Database) {
    const rows: [string, CropName | 'gone', number][] = [['u1', 'a', 1], ['u1', 'gone', 2], ['u1', 'c', 3], ['u2', 'b', 4]];
    for (const [user, crop, m] of rows) {
      oldDb.prepare('INSERT INTO favourites(user_id,unit,at) VALUES(?,?,?)').run(user, crop === 'gone' ? 'hk:gone' : crops[crop].text, minute(m));
      atlas.prepare('INSERT INTO favourites(user,crop,at) VALUES(?,?,?)').run(user, crop === 'gone' ? cropId(P, 2) : crops[crop].id, ms(m));
    }
    oldDb.exec(`INSERT INTO unit_redirects(id,target) VALUES('hk:gone','hk:b')`);
  }
  it('list the same crops, newest first, a retired one as its replacement', async () => {
    const { oldDb, atlas, oldEnv, env } = setup();
    starred(oldDb, atlas);
    const oldItems = async (_env: Env, ids: string[]) => new Map(ids.filter(id => id !== 'hk:gone').map(id => [id, { id }]));
    const oldReplacement = async (_env: Env, id: string) => id === 'hk:gone' ? 'hk:b' : null;
    expect((await favouriteIds(env, 'u1')).ids).toEqual((await oldFavouriteIds(oldEnv, 'u1')).ids.map(id => textToPublic.get(id) ?? publicId(cropId(P, 2))));
    for (const [offset, limit] of [[0, 60], [0, 2], [2, 2]]) {
      const before = await oldFavouriteCrops(oldEnv, 'u1', offset, limit, oldItems, oldReplacement);
      const after = await favouriteCrops(env, 'u1', offset, limit, cropItems);
      expect({ ...after, items: after.items.map(i => i.source_id) }).toEqual({ ...before, items: before.items.map(i => i.id) });
    }
    expect((await favouriteCrops(env, 'u3', 0, 60, cropItems))).toEqual({ items: [], total: 0, offset: 0, next: null });
  });
  it('star and unstar by public id, refuse an unknown crop, and send a stale text id to reload', async () => {
    const { env } = setup();
    expect(await setFavourite(env, 'u3', { crop: publicId(crops.a.id), favourite: true })).toEqual({ crop: publicId(crops.a.id), favourite: true });
    expect((await favouriteIds(env, 'u3')).ids).toEqual([publicId(crops.a.id)]);
    expect(await setFavourite(env, 'u3', { crop: publicId(crops.a.id), favourite: false })).toEqual({ crop: publicId(crops.a.id), favourite: false });
    expect((await favouriteIds(env, 'u3')).ids).toEqual([]);
    for (const crop of [publicId(cropId(P, 2)), 'nonsense!']) await expect(setFavourite(env, 'u3', { crop, favourite: true })).rejects.toBeInstanceOf(FavouriteError);
    await expect(setFavourite(env, 'u3', { crop: 'a' })).rejects.toBeInstanceOf(FavouriteError);
    await expect(setFavourite(env, 'u3', { crop: 'hk:unknown', favourite: true })).rejects.toBeInstanceOf(StaleCrop);
  });
  it(`hold at most ${FAVOURITES_HELD} crops a user`, async () => {
    const { atlas, env } = setup();
    const star = atlas.prepare('INSERT INTO favourites(user,crop,at) VALUES(?,?,?)');
    for (let i = 0; i < FAVOURITES_HELD; i++) star.run('u3', cropId(9000, i), i);
    await expect(setFavourite(env, 'u3', { crop: publicId(crops.a.id), favourite: true })).rejects.toMatchObject({ status: 429 });
  });
  it('read by the user and time index', () => {
    const { atlas } = setup();
    for (const [sql, values] of [[favouriteIdsQuery(), ['u1', 10]], [favouritePageQuery(), ['u1', 10, 0]], [favouriteCountQuery(), ['u1']]] as const) {
      const details = plan(atlas, sql, [...values]);
      expect(scans(details, ['favourites'])).toEqual([]);
      expect(details.some(d => d.includes('TEMP B-TREE'))).toBe(false);
    }
  });
});

describe('the admin lists', () => {
  for (const params of [{}, { kind: 'accounts' }, { kind: 'anonymous' }, { kind: 'admins' }, { kind: 'banned' }, { q: 'Ada' }, { kind: 'accounts', q: 'bea' },
    { kind: 'unclaimed' }, { kind: 'unclaimed', q: '0f' }, { kind: 'accounts', offset: '1' }]) it(`list reviewers ${JSON.stringify(params)} as before`, async () => {
    const { oldDb, oldEnv, env } = setup();
    // Two of them saved nothing in the old site's first week: the third account and a legacy id.
    oldDb.exec(`UPDATE "user" SET banned=1 WHERE id='u2'`);
    (env as any).ACCOUNTS.prepare(`UPDATE "user" SET banned=1 WHERE id='u2'`).run();
    const before = JSON.parse(JSON.stringify(await oldReviewers(oldEnv, new URLSearchParams(params))));
    const after = JSON.parse(JSON.stringify(await reviewers(env, new URLSearchParams(params))));
    expect(after).toEqual(before);
  });
  for (const who of [{ user: 'u1' }, { user: 'u2' }, { actor: 'reviewer-0000000f' }, { user: 'u3' }]) it(`list ${JSON.stringify(who)}'s submissions as before`, async () => {
    const { oldEnv, env } = setup();
    const actorsOld = 'user' in who ? ['(SELECT actor FROM actors WHERE user_id=?)', [who.user]] as [string, string[]] : ['(?)', [who.actor]] as [string, string[]];
    const before = await oldSubmissions(oldEnv, new URLSearchParams(), actorsOld);
    const after = await submissions(env, new URLSearchParams(), await reviewerActors(env, who, tools));
    expect(after).toEqual({ ...before, items: before.items.map((s: Json) => ({ ...s,
      targets: s.targets.map((t: Json) => ({ ...t, id: textToPublic.get(t.id) })) })) });
  });
  it('say what each kind of submission asked for', () => {
    expect(summary(canonical({ target: null, input: { label: 'あ', answers: [{ verdict: 'match' }, { verdict: 'wrong' }, { verdict: 'match' }], skipped: [{}] } })))
      .toEqual({ kind: 'round', label: 'あ', verdicts: { match: 2, wrong: 1 }, skipped: 1 });
    expect(summary(canonical({ target: '@batch', input: { character: 'い', crops: [{}, {}] } })))
      .toEqual({ kind: 'correction', label: 'い', verdicts: { wrong: 2 } });
    expect(summary(canonical({ target: '@flags', input: { finding: 'box-relabel', glyphs: [{}] } })))
      .toEqual({ kind: 'flag', label: 'box-relabel', verdicts: { wrong: 1 } });
    expect(summary(canonical({ target: 'u1', input: { verdict: 'wrong', issue: 'character', character: 'う' } })))
      .toEqual({ kind: 'crop', target: 'u1', label: 'う', verdicts: { wrong: 1 } });
  });
  it('read by index', () => {
    const { atlas } = setup();
    const checks: [string, unknown[]][] = [[activeUsersQuery(), []], [userWorkQuery(), ['["u1"]']], [unclaimedQuery(), ['', 51, 0]],
      [submissionsQuery(), ['[1,2]', 51, 0]], [rejectNamedQuery(), ['["u1:s1"]']], [rejectReviewerQuery(), ['[1]', 1e15, 0, 40]],
      [flagRowsQuery(), ['[1]']], [flaggedCorpusQuery(), []]];
    for (const [sql, values] of checks)
      expect(scans(plan(atlas, sql, values), ['events', 'submissions', 'crops', 'rejections', 'corpus_rows', 'corpus_flags'])).toEqual([]);
    expect(plan(atlas, flaggedCorpusQuery()).some(d => d.includes('crop_corpus_flagged'))).toBe(true);
  });
});

describe('corpus flags', () => {
  const glyph = { id: 'hl:a:1', character: '𛁈', realigned: '所', transcribed: 'シ' };
  it('take a finding and the glyphs it found in doubt', () => {
    expect(flagRequest({ finding: 'box-relabel', glyphs: [glyph, { id: 'hl:a:2', character: 'と' }] }, tools)).toEqual({ finding: 'box-relabel',
      glyphs: [glyph, { id: 'hl:a:2', character: 'と', realigned: null, transcribed: null }] });
  });
  it('refuse a request without a finding, a glyph twice, a glyph without its character, or too many glyphs', () => {
    for (const input of [{ glyphs: [glyph] }, { finding: 'x', glyphs: [] }, { finding: 'x', glyphs: [glyph, glyph] },
      { finding: 'x', glyphs: [{ id: 'hl:a:1' }] }, { finding: 'x', glyphs: Array.from({ length: 97 }, (_, i) => ({ id: `g${i}`, character: 'と' })) }])
      expect(() => flagRequest(input, tools)).toThrow();
  });
  it('list flagged corpus glyphs in the order their rows were first written, as before', async () => {
    const oldDb = migrated('migrations'), atlas = withUndo(migrated('migrations-atlas'));
    atlas.exec(`INSERT INTO books(id,key) VALUES(1,'b'); INSERT INTO pages(id,key,book,rev) VALUES(${P},'p',1,1);`);
    // Glyphs named in this order; some flagged since, one flagged by a review before the others.
    const named = [{ slot: 3, state: 'flagged', flag: null }, { slot: 1, state: 'checked', flag: null }, { slot: 7, state: 'flagged', flag: 'box-relabel' },
      { slot: 2, state: 'pending', flag: null }, { slot: 5, state: 'flagged', flag: 'box-relabel' }];
    const states: Record<string, number> = { pending: 0, checked: 1, flagged: 2 };
    named.forEach((g, i) => {
      const id = cropId(P, g.slot);
      oldDb.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
        VALUES(?,'corpus','あ','handwritten','kana',?,1,1,1,0,?,'{}','{}','{}')`).run(`c:${g.slot}`, g.state,
        JSON.stringify(g.flag ? { flag: { finding: g.flag, realigned: 'い', transcribed: null, at: minute(i) } } : {}));
      atlas.prepare('INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(?,1,1,?,1,0)').run(id, states[g.state]);
      atlas.prepare('INSERT INTO corpus_rows(crop,seq) VALUES(?,?)').run(id, i + 1);
      if (g.flag) atlas.prepare(`INSERT INTO corpus_flags(crop,finding,realigned,transcribed,at) VALUES(?,?,'い',NULL,?)`).run(id, g.flag, ms(i));
    });
    // A flagged corpus glyph never named before, which the old site could not hold: it lists last.
    atlas.prepare('INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(?,1,1,2,1,0)').run(cropId(P, 0));
    const before = (oldDb.query(`SELECT id,data FROM units WHERE origin='corpus' AND state='flagged' ORDER BY rowid LIMIT 96`).all() as Json[])
      .map(r => [publicId(cropId(P, Number(r.id.split(':')[1]))), JSON.parse(r.data).flag ?? null]);
    const env = { ATLAS: d1(atlas) } as unknown as Env;
    const items = async (_env: Env, ids: number[]) => ids.map(id => ({ id: publicId(id) }));
    const after = await flaggedCorpus(env, items);
    expect(after.items.map((i: Json) => [i.id, i.flag ?? null])).toEqual([...before, [publicId(cropId(P, 0)), null]]);
    expect(after.total).toBe(4);
  });
});
