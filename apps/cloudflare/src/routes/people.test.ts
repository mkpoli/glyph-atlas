// The people routes on ATLAS and ACCOUNTS: favourites, the history, the ranking and the admin lists,
// on one set of reviews. Answers pinned in __snapshots__ are those the old database's routes gave at
// the cutover.
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
import { activeActorsQuery, flagRequest, newerUsersQuery, savedUsersQuery, flagRowsQuery, flaggedCorpus, flaggedCorpusQuery, rejectNamedQuery, rejectReviewerQuery,
  reviewerActors, reviewers, submissions, submissionsQuery, summary, unclaimedQuery, userWorkQuery } from './admin';

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

// One set of people, crops and reviews.
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
  const fresh = newDatabases();
  const MEDIA = media({ [P]: [{ slot: 0, id: 'hk:a' }, { slot: 1, id: 'hk:b' }, { slot: 2, id: 'hk:gone', retired: true, replaced_by: crops.b.id }],
    [Q]: [{ slot: 0, id: 'hk:c' }] });
  return { ...fresh,
    env: { ATLAS: d1(fresh.atlas), ACCOUNTS: d1(fresh.accounts), MEDIA } as unknown as Env };
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
    const { env } = setup();
    for (const limit of ['40', '2'])
      expect(await pages(b => history(env, new URLSearchParams({ ...params, limit, ...(b ? { before: b } : {}) }), 'u1', tools))).toMatchSnapshot();
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
    for (const bad of ['not-base64!!', btoa('[1,2]'), btoa('[1,2,3]'), btoa('["1",0,3]'), btoa('["soon","cf:x"]')]) expect(() => decodeCursor(bad)).toThrow('Invalid cursor.');
  });

  it('goes on from a page loaded before the cutover, from before its last row\'s time', async () => {
    const { env } = setup();
    // The old cursor: the last row's time and its event id.
    const legacy = btoa(JSON.stringify([minute(5), 'cf:e5']));
    expect(decodeCursor(legacy)).toEqual({ at: ms(5), src: 0, id: 0 });
    const { items } = await history(env, new URLSearchParams({ before: legacy }), null, tools);
    expect(items.map((i: Json) => i.id)).toEqual(['4', '3', '2', 'reviewer-0000000a:r1', '1']);
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
  function ledger(db: Database) {
    // u2 claims a form, then another, which retracts the first; renames a crop by a review its claim
    // names; u1 claims one under its legacy id and u2 accepts it twice; a source's and a model's claims
    // are nobody's, and so is the one moved from a written form.
    const at = ms(1);
    db.exec(`INSERT INTO written_forms(id,submission,crop,actor,revision,pixels,label,form,request,at)
        VALUES('w1','u2:w',1,'u2',1,'p','あ','あ','{}',${at});
      INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note,crops)
        VALUES('d1','${minute(1)}','reviewer-0000000a','cluster','U+3042','𛀂','c','r','[]','','[]');
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
    const { atlas, env, subId } = setup();
    ledger(atlas);
    // The renaming review the claim names, standing.
    atlas.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(${subId.size + 1},'u2:rename',3,'{}','{}',${ms(1)})`);
    const after = await rankingRows(env);
    expect(after).toMatchSnapshot();
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
  function starred(atlas: Database) {
    const rows: [string, CropName | 'gone', number][] = [['u1', 'a', 1], ['u1', 'gone', 2], ['u1', 'c', 3], ['u2', 'b', 4]];
    for (const [user, crop, m] of rows) {
      atlas.prepare('INSERT INTO favourites(user,crop,at) VALUES(?,?,?)').run(user, crop === 'gone' ? cropId(P, 2) : crops[crop].id, ms(m));
    }
  }
  it('list the same crops, newest first, a retired one as its replacement', async () => {
    const { atlas, env } = setup();
    starred(atlas);
    expect((await favouriteIds(env, 'u1')).ids).toEqual([crops.c.id, cropId(P, 2), crops.a.id].map(publicId));
    const read = [];
    for (const [offset, limit] of [[0, 60], [0, 2], [2, 2]]) {
      const after = await favouriteCrops(env, 'u1', offset, limit, cropItems);
      read.push({ ...after, items: after.items.map(i => i.source_id) });
    }
    expect(read).toEqual([{ items: ['hk:c', 'hk:b', 'hk:a'], total: 3, offset: 0, next: null }, { items: ['hk:c', 'hk:b'], total: 3, offset: 0, next: 2 },
      { items: ['hk:a'], total: 3, offset: 2, next: null }]);
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
    { kind: 'unclaimed' }, { kind: 'unclaimed', q: '0f' }]) it(`list reviewers ${JSON.stringify(params)} as before`, async () => {
    const { env } = setup();
    // Two of them saved nothing in the old site's first week: the third account and a legacy id.
    (env as any).ACCOUNTS.prepare(`UPDATE "user" SET banned=1 WHERE id='u2'`).run();
    expect(JSON.parse(JSON.stringify(await reviewers(env, new URLSearchParams(params))))).toMatchSnapshot();
  });
  it('walk the reviewers a page at a time, each once, the active first, bounded in rows read', async () => {
    const { atlas, accounts, env } = setup();
    // 120 more accounts: the even ones saved something, at times out of their order of creation.
    const user = accounts.prepare(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt,isAnonymous) VALUES(?,?,?,1,?,?,0)`);
    for (let i = 0; i < 120; i++) {
      const id = `p${String(i).padStart(3, '0')}`, created = `2026-09-${String(1 + (i % 28)).padStart(2, '0')}T00:00:${String(i % 60).padStart(2, '0')}.000Z`;
      user.run(id, `person ${i}`, `${id}@x`, created, created);
      if (i % 2) continue;
      atlas.prepare(`INSERT INTO actors(id,key,user,via,at) VALUES(?,?,?,'account',0)`).run(100 + i, id, id);
      atlas.prepare(`INSERT INTO submissions(key,actor,request,response,at) VALUES(?,?,'{}','{}',?)`).run(`${id}:s`, 100 + i, ms(20) + ((i * 37) % 120) * 1000);
      atlas.prepare(`INSERT INTO actor_stats(actor,reviews,rounds,at) VALUES(?,0,1,?)`).run(100 + i, ms(20) + ((i * 37) % 120) * 1000);
    }
    for (const kind of ['active', 'accounts']) {
      const seen: Json[] = [];
      let offset: string | null = '0';
      do {
        const page: Json = await reviewers(env, new URLSearchParams({ kind, offset }));
        expect(page.items.length).toBeLessThanOrEqual(50);
        seen.push(...page.items);
        offset = page.next;
      } while (offset);
      const ids = seen.map(r => r.id);
      expect(new Set(ids).size).toBe(ids.length);
      const active = seen.filter(r => r.last !== null);
      // u2 is anonymous, so not among the accounts.
      expect(active.length).toBe(kind === 'active' ? 60 + 2 : 60 + 1);
      expect(active.map(r => r.last)).toEqual([...active.map(r => r.last)].sort().reverse());
      expect(seen.slice(0, active.length)).toEqual(active);
      if (kind === 'accounts') {
        const rest = seen.slice(active.length);
        expect(rest.length).toBe(60 + 1);
        expect(rest.map(r => r.createdAt)).toEqual([...rest.map(r => r.createdAt)].sort().reverse());
      } else expect(seen.length).toBe(active.length);
    }
    await expect(reviewers(env, new URLSearchParams({ offset: 'nonsense' }))).rejects.toMatchObject({ status: 422 });
  });
  for (const who of [{ user: 'u1' }, { user: 'u2' }, { actor: 'reviewer-0000000f' }, { user: 'u3' }]) it(`list ${JSON.stringify(who)}'s submissions as before`, async () => {
    const { env } = setup();
    expect(await submissions(env, new URLSearchParams(), await reviewerActors(env, who, tools))).toMatchSnapshot();
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
    const checks: [string, unknown[]][] = [[activeActorsQuery(), [1e15, 0, 100]], [userWorkQuery(), ['["u1"]']], [unclaimedQuery(), [1e15, 0, '', 51]],
      [savedUsersQuery(), ['["u1"]']],
      [submissionsQuery(), ['[1,2]', 51, 0]], [rejectNamedQuery(), ['["u1:s1"]']], [rejectReviewerQuery(), ['[1]', 1e15, 0, 40]],
      [flagRowsQuery(), ['[1]']], [flaggedCorpusQuery(), []]];
    for (const [sql, values] of checks)
      expect(scans(plan(atlas, sql, values), ['events', 'submissions', 'crops', 'rejections', 'corpus_rows', 'corpus_flags'])).toEqual([]);
    expect(plan(atlas, flaggedCorpusQuery()).some(d => d.includes('crop_corpus_flagged'))).toBe(true);
    expect(scans(plan(atlas, activeActorsQuery(), [1e15, 0, 100]), ['actor_stats', 'actors'])).toEqual([]);
    const { accounts } = setup();
    expect(scans(plan(accounts, newerUsersQuery('accounts'), ['', '\uffff', '', 100]), ['user'])).toEqual([]);
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
    const atlas = withUndo(migrated('migrations-atlas'));
    atlas.exec(`INSERT INTO books(id,key) VALUES(1,'b'); INSERT INTO pages(id,key,book,rev) VALUES(${P},'p',1,1);`);
    // Glyphs named in this order; some flagged since, one flagged by a review before the others.
    const named = [{ slot: 3, state: 'flagged', flag: null }, { slot: 1, state: 'checked', flag: null }, { slot: 7, state: 'flagged', flag: 'box-relabel' },
      { slot: 2, state: 'pending', flag: null }, { slot: 5, state: 'flagged', flag: 'box-relabel' }];
    const states: Record<string, number> = { pending: 0, checked: 1, flagged: 2 };
    named.forEach((g, i) => {
      const id = cropId(P, g.slot);
      atlas.prepare('INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(?,1,1,?,1,0)').run(id, states[g.state]);
      atlas.prepare('INSERT INTO corpus_rows(crop,seq) VALUES(?,?)').run(id, i + 1);
      if (g.flag) atlas.prepare(`INSERT INTO corpus_flags(crop,finding,realigned,transcribed,at) VALUES(?,?,'い',NULL,?)`).run(id, g.flag, ms(i));
    });
    // A flagged corpus glyph never named before, which the old site could not hold: it lists last.
    atlas.prepare('INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(?,1,1,2,1,0)').run(cropId(P, 0));
    const flag = (at: number) => ({ finding: 'box-relabel', realigned: 'い', transcribed: null, at: minute(at) });
    const before = [[publicId(cropId(P, 3)), null], [publicId(cropId(P, 7)), flag(2)], [publicId(cropId(P, 5)), flag(4)]];
    const env = { ATLAS: d1(atlas) } as unknown as Env;
    const items = async (_env: Env, ids: number[]) => ids.map(id => ({ id: publicId(id) }));
    const after = await flaggedCorpus(env, items);
    expect(after.items.map((i: Json) => [i.id, i.flag ?? null])).toEqual([...before, [publicId(cropId(P, 0)), null]]);
    expect(after.total).toBe(4);
    const unreadable = await flaggedCorpus(env, async (_env, ids) => ids.filter(id => id !== cropId(P, 7)).map(id => ({ id: publicId(id) })));
    expect([unreadable.items.length, unreadable.total]).toEqual([3, 3]);
  });
});
