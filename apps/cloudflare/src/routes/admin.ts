// What the admin page reads and writes: who has saved work, each reviewer's submissions, rejecting
// them, and corpus glyphs a check outside the site flagged. Users and their names are in ACCOUNTS; what
// they saved is in ATLAS under their actor ids, so each list reads one database and then the other,
// by bounded id lists (accounts.ts). Times are ISO strings and crops public ids in the answers.
import { cropOf, publicId } from '../atlas3';
import { IN_LIST, people } from '../accounts';
import { aliasCrop } from '../atlas3Aliases';
import { Problem } from '../busy';
import { actorFor, cropChanges, revert, type Submission } from './review';

type Json = Record<string, any>;
export type AdminTools = { text: (value: unknown, max: number, name: string, required?: boolean) => string | null };
const PAGE = 50;
// The crops a submission lists by name, to open from the admin page; a larger one says how many more.
const SHOWN = 24;
// Accounts read a statement at a time while a list is walked.
const WALK = IN_LIST;

const iso = (at: number | null) => at === null || at === undefined ? null : new Date(at).toISOString();
const page = (rows: Json[], offset: number) => ({ items: rows.slice(0, PAGE), next: rows.length > PAGE ? offset + PAGE : null });

// The kind codes of a person's review: a verdict, never an undo, a mark or a flag.
export const REVIEW_KINDS = `(SELECT code FROM codes WHERE field='kind' AND key IN ('match','wrong','unsure'))`;

// Each user with saved work, the most recently active first: one row per actor that has a user, its
// latest submission a seek in `submission_actor`.
export const activeUsersQuery = () => `SELECT user,max(last) AS last FROM (SELECT user,(SELECT max(at) FROM submissions WHERE actor=actors.id) AS last
  FROM actors WHERE user IS NOT NULL) GROUP BY user HAVING last IS NOT NULL ORDER BY last DESC,user`;
// What each user of a page saved, by their actors.
export const userWorkQuery = () => `SELECT a.user,count(s.id) AS submissions,max(s.at) AS last,count(r.submission) AS rejected
  FROM actors a JOIN submissions s ON s.actor=a.id LEFT JOIN rejections r ON r.submission=s.id
  WHERE a.user IN (SELECT value FROM json_each(?)) GROUP BY a.user`;
export const unclaimedQuery = () => `SELECT a.key AS actor,count(*) AS submissions,max(s.at) AS last FROM actors a JOIN submissions s ON s.actor=a.id
  WHERE a.user IS NULL AND (?1='' OR a.key LIKE '%'||?1||'%') GROUP BY a.id ORDER BY last DESC,a.key LIMIT ?2 OFFSET ?3`;

const USER_COLUMNS = 'id,name,email,image,role,banned,banReason,isAnonymous,createdAt,lastLoginMethod';
function userFilter(kind: string): string {
  const where = [`(?1='' OR name LIKE '%'||?1||'%' OR email LIKE '%'||?1||'%' OR id=?1)`];
  if (kind === 'accounts') where.push('coalesce(isAnonymous,0)=0');
  if (kind === 'anonymous') where.push('isAnonymous=1');
  if (kind === 'banned') where.push('banned=1');
  if (kind === 'admins') where.push(`role='admin'`);
  return where.join(' AND ');
}

/**
 * Reviewers, the most recently active first: users that saved something (or every account, for
 * `accounts` and the other kinds, those with no saved work after, newest account first), and the ids
 * from before accounts that nobody has claimed (`unclaimed`).
 */
export async function reviewers(env: Env, q: URLSearchParams) {
  const kind = q.get('kind') ?? 'active', offset = Math.max(0, Number(q.get('offset') ?? 0) | 0);
  const find = (q.get('q') ?? '').trim().slice(0, 64);
  if (kind === 'unclaimed') {
    const rows = await env.ATLAS.prepare(unclaimedQuery()).bind(find, PAGE + 1, offset).all<Json>();
    return page(rows.results.map(r => ({ actor: r.actor, name: r.actor, submissions: r.submissions, last: iso(r.last) })), offset);
  }
  const wanted = offset + PAGE + 1, filter = userFilter(kind), found: Json[] = [];
  const active = (await env.ATLAS.prepare(activeUsersQuery()).all<{ user: string; last: number }>()).results;
  const last = new Map(active.map(row => [row.user, row.last]));
  // The active users in order, each part's accounts read under the filter.
  for (let i = 0; i < active.length && found.length < wanted; i += WALK) {
    const part = active.slice(i, i + WALK).map(row => row.user);
    const rows = (await env.ACCOUNTS.prepare(`SELECT ${USER_COLUMNS} FROM "user" WHERE ${filter} AND id IN (SELECT value FROM json_each(?2))`)
      .bind(find, JSON.stringify(part)).all<Json>()).results;
    rows.sort((a, b) => last.get(b.id)! - last.get(a.id)! || String(b.createdAt).localeCompare(String(a.createdAt)));
    found.push(...rows);
  }
  // Then accounts with no saved work, newest first.
  if (kind !== 'active') for (let skip = 0; found.length < wanted; skip += WALK) {
    const rows = (await env.ACCOUNTS.prepare(`SELECT ${USER_COLUMNS} FROM "user" WHERE ${filter} ORDER BY createdAt DESC,id LIMIT ?2 OFFSET ?3`)
      .bind(find, WALK, skip).all<Json>()).results;
    found.push(...rows.filter(row => !last.has(row.id)));
    if (rows.length < WALK) break;
  }
  const shown = found.slice(offset, wanted);
  const work = new Map(shown.length ? (await env.ATLAS.prepare(userWorkQuery()).bind(JSON.stringify(shown.map(r => r.id))).all<Json>())
    .results.map(row => [row.user, row]) : []);
  return page(shown.map(r => {
    const w = work.get(r.id);
    return { id: r.id, name: r.name,
      // An anonymous user's address is a placeholder, not one anybody reads.
      email: r.isAnonymous ? null : r.email, image: r.image, role: r.role, banned: Boolean(r.banned), banReason: r.banReason,
      createdAt: r.createdAt, lastLoginMethod: r.lastLoginMethod, submissions: w?.submissions ?? 0, last: iso(w?.last ?? null),
      rejected: w?.rejected ?? 0, anonymous: Boolean(r.isAnonymous) };
  }), offset);
}

/** The actor ids of one reviewer: a user's, or one id from before accounts that nobody holds. */
export async function reviewerActors(env: Env, input: Json, tools: AdminTools): Promise<number[]> {
  if (typeof input.user === 'string') return (await env.ATLAS.prepare('SELECT id FROM actors WHERE user=?')
    .bind(tools.text(input.user, 64, 'user', true)).all<{ id: number }>()).results.map(r => r.id);
  if (typeof input.actor === 'string') return (await env.ATLAS.prepare('SELECT id FROM actors WHERE key=?')
    .bind(tools.text(input.actor, 128, 'actor', true)).all<{ id: number }>()).results.map(r => r.id);
  throw new Problem(422, 'Name a user or a reviewer id.');
}

// One reviewer's submissions, newest first, each with its review count and its first crops by the
// label each was shown under.
export const submissionsQuery = () => `SELECT s.id,s.key,s.at,s.undone,s.request,r.by,r.reason,r.at AS rejected_at,
    (SELECT count(*) FROM events e WHERE e.submission=s.id AND e.kind IN ${REVIEW_KINDS}) AS crops,
    (SELECT json_group_array(json_object('id',crop,'label',label)) FROM (SELECT e.crop,g.text AS label
      FROM events e LEFT JOIN graphemes g ON g.id=e.shown WHERE e.submission=s.id AND e.kind IN ${REVIEW_KINDS} ORDER BY e.id LIMIT ${SHOWN})) AS targets
  FROM submissions s LEFT JOIN rejections r ON r.submission=s.id
  WHERE s.actor IN (SELECT value FROM json_each(?1)) ORDER BY s.at DESC,s.id DESC LIMIT ?2 OFFSET ?3`;

/** One reviewer's submissions, newest first, each with what it decided and whether it still stands. */
export async function submissions(env: Env, q: URLSearchParams, actors: number[]) {
  const offset = Math.max(0, Number(q.get('offset') ?? 0) | 0);
  const rows = actors.length ? (await env.ATLAS.prepare(submissionsQuery()).bind(JSON.stringify(actors), PAGE + 1, offset).all<Json>()).results : [];
  const named = await people(env, rows.map(row => row.by));
  return page(rows.map(row => ({ id: row.key, at: iso(row.at), crops: row.crops, ...summary(row.request),
    targets: (JSON.parse(row.targets ?? '[]') as { id: number; label: string | null }[]).map(t => ({ id: publicId(t.id), label: t.label })),
    state: row.by ? 'rejected' : row.undone ? 'undone' : 'standing',
    rejection: row.by ? { by: named.get(row.by)?.name ?? row.by, reason: row.reason, at: iso(row.rejected_at) } : null })), offset);
}

/** What a submission asked for, from the request it was saved with. */
export function summary(request: string): Json {
  let signed: Json;
  try { signed = JSON.parse(request) } catch { return { kind: 'other' } }
  const input = signed.input ?? {}, target = signed.target;
  const verdicts = (answers: Json[] = []) => answers.reduce((counts: Json, answer) => {
    const key = answer?.verdict ?? 'other'; counts[key] = (counts[key] ?? 0) + 1; return counts }, {});
  if (target === '@batch') return { kind: 'correction', label: input.character ?? null, verdicts: { wrong: input.crops?.length ?? 0 } };
  if (target === FLAGS) return { kind: 'flag', label: input.finding ?? null, verdicts: { wrong: input.glyphs?.length ?? 0 } };
  if (target === null) return { kind: 'round', label: input.label ?? null, verdicts: verdicts(input.answers),
    skipped: input.skipped?.length ?? 0 };
  return { kind: 'crop', target, label: input.character ?? input.reading ?? null, verdicts: verdicts([input]) };
}

// Cursors over one reviewer's submissions: (at, id) of the last one a page took.
const encodeCursor = (at: number, id: number) => btoa(JSON.stringify([at, id]));
function decodeCursor(value: string): { at: number; id: number } {
  try {
    const d = JSON.parse(atob(value));
    if (!Array.isArray(d) || d.length !== 2 || !d.every(Number.isSafeInteger)) throw 0;
    return { at: d[0], id: d[1] };
  } catch { throw new Problem(422, 'Invalid cursor.') }
}

// Reject an admin's selection: the named submissions, or all of one reviewer's a page at a time.
// They go newest first, so a crop two of them touched goes back step by step. One that a later
// review by someone else has built on is left as it is and reported; `next` continues after the page.
const REJECT_PAGE = 40;
// Each crop a rejection puts back costs a few queries, and one request may make a thousand: a page
// stops before the crops it has put back pass this many.
const REJECT_CROPS = 250;
// An admin's flag request is a submission too, but no review: nothing puts a flag back, so a rejection
// never takes one up.
const FLAG_REQUEST = `iif(json_valid(request),json_extract(request,'$.target'),NULL)='@flags'`;
const SUBMISSION = `id,key,actor,at,undone,(SELECT count(*) FROM events e WHERE e.submission=submissions.id AND e.kind IN ${REVIEW_KINDS}) AS crops,
  ${FLAG_REQUEST} AS flag`;
export const rejectNamedQuery = () => `SELECT ${SUBMISSION} FROM submissions WHERE key IN (SELECT value FROM json_each(?)) AND undone=0
  ORDER BY at DESC,id DESC`;
export const rejectReviewerQuery = () => `SELECT ${SUBMISSION} FROM submissions WHERE undone=0 AND actor IN (SELECT value FROM json_each(?1))
  AND (at,id)<(?2,?3) AND NOT coalesce(${FLAG_REQUEST},0) ORDER BY at DESC,id DESC LIMIT ?4`;

export async function reject(env: Env, admin: string, input: Json, tools: AdminTools) {
  const reason = tools.text(input.reason ?? '', 500, 'reason') ?? '';
  let rows: (Submission & { crops: number })[], reviewer: number[] | undefined;
  if (Array.isArray(input.submissions)) {
    const keys = input.submissions.slice(0, REJECT_PAGE).map((key: unknown) => tools.text(key, 256, 'submission', true)!);
    if (!keys.length) throw new Problem(422, 'Name the submissions to reject.');
    rows = (await env.ATLAS.prepare(rejectNamedQuery()).bind(JSON.stringify(keys)).all<Submission & { crops: number; flag: number }>()).results;
    if (rows.some(row => (row as { flag?: number }).flag)) throw new Problem(409, 'A flag is not a review, so it cannot be rejected.');
  } else {
    reviewer = await reviewerActors(env, input, tools);
    const before = input.next ? decodeCursor(tools.text(input.next, 512, 'cursor', true)!) : { at: Number.MAX_SAFE_INTEGER, id: 0 };
    rows = (await env.ATLAS.prepare(rejectReviewerQuery()).bind(JSON.stringify(reviewer), before.at, before.id, REJECT_PAGE)
      .all<Submission & { crops: number }>()).results;
  }
  let rejected = 0, spent = 0;
  const conflicts: string[] = [], unavailable: string[] = [], done: (Submission & { crops: number })[] = [];
  const actor = rows.length ? await actorFor(env, admin) : 0;
  for (const submission of rows) {
    if (spent && spent + submission.crops > REJECT_CROPS) break;
    spent += submission.crops; done.push(submission);
    try { await revert(env, submission, actor, { by: admin, reason, reviewer }); rejected++ }
    catch (error) {
      // A crop someone else has changed since stays as it is; a crop no longer in the collection
      // cannot be put back. Either way the rest go on.
      if (error instanceof Problem && error.status === 409) conflicts.push(submission.key);
      else if (error instanceof Problem && error.status === 404) unavailable.push(submission.key);
      else throw error;
    }
  }
  const last = done.at(-1), more = done.length < rows.length || (!Array.isArray(input.submissions) && rows.length === REJECT_PAGE);
  return { rejected, conflicts, unavailable,
    next: !Array.isArray(input.submissions) && more && last ? encodeCursor(last.at, last.id) : null,
    left: Array.isArray(input.submissions) ? rows.slice(done.length).map(row => row.key) : [] };
}

// Corpus glyphs a check made outside the site found in doubt, sent to the Flagged view by an admin: the
// boxes `atlas repair relabel` would label otherwise than a form a person decided. Each is flagged with
// a wrong-character issue by an event of kind 'flag', under a submission holding the admin's request,
// with the finding in `corpus_flags`, so it waits among the corpus glyphs `/atlas/corpus/reviews` lists
// until a person settles it. A glyph anything was written about already (`corpus_rows`), or a collected
// crop, is left as it is (`held`), so what a round or a review said stands and a list sent again
// writes nothing. One whose character is no longer the one the finding was made against (`changed`), or
// whose image this site may not serve (`unshowable`), is left out.
const FLAG_MAX = 96;
const FLAGS = '@flags';
export function flagRequest(input: Json, tools: AdminTools) {
  const finding = tools.text(input?.finding, 64, 'finding', true)!;
  if (!Array.isArray(input.glyphs) || !input.glyphs.length || input.glyphs.length > FLAG_MAX) throw new Problem(422, `Name 1 to ${FLAG_MAX} glyphs.`);
  const glyphs = input.glyphs.map((g: Json) => ({ id: tools.text(g?.id, 512, 'glyph id', true)!, character: tools.text(g?.character, 16, 'character', true)!,
    realigned: tools.text(g?.realigned, 16, 'realigned character'), transcribed: tools.text(g?.transcribed, 16, 'transcribed character') }));
  if (new Set(glyphs.map((g: Json) => g.id)).size !== glyphs.length) throw new Problem(422, 'A glyph is named twice.');
  return { finding, glyphs } as { finding: string; glyphs: { id: string; character: string; realigned: string | null; transcribed: string | null }[] };
}

// Each named crop's kind, character, revision, cut, state, withheld flag, and whether anything was
// written about it: primary-key lookups.
export const flagRowsQuery = () => `SELECT c.id,c.kind,g.text AS char,c.revision,c.cut,c.quiz,EXISTS(SELECT 1 FROM corpus_rows r WHERE r.crop=c.id) AS held
  FROM json_each(?) j JOIN crops c ON c.id=j.value LEFT JOIN graphemes g ON g.id=c.char`;
export const FLAGGED_STATE = 2;

export async function flagCorpus(env: Env, admin: string, input: Json, tools: AdminTools) {
  const { finding, glyphs } = flagRequest(input, tools);
  const ids = await Promise.all(glyphs.map(g => g.id.includes(':') ? aliasCrop(env, g.id) : Promise.resolve(cropOf(g.id))));
  const rows = new Map((await env.ATLAS.prepare(flagRowsQuery()).bind(JSON.stringify(ids.filter(id => id !== null))).all<Json>())
    .results.map(row => [row.id as number, row]));
  const at = Date.now(), out: Record<string, string[]> = { flagged: [], held: [], changed: [], unshowable: [], missing: [] };
  const flagged: { glyph: (typeof glyphs)[number]; row: Json }[] = [];
  glyphs.forEach((g, i) => {
    const row = ids[i] === null ? undefined : rows.get(ids[i]!);
    if (!row) { out.missing.push(g.id); return }
    if (row.kind !== 1 || row.held) { out.held.push(g.id); return }
    if (row.char !== g.character) { out.changed.push(g.id); return }
    if (!row.quiz) { out.unshowable.push(g.id); return }
    flagged.push({ glyph: g, row });
    out.flagged.push(g.id);
  });
  if (!flagged.length) return { ...out, rows_written: 0 };
  const actor = await actorFor(env, admin), key = `${admin}:${crypto.randomUUID()}`;
  const result = { ...out, rows_written: 0 };
  const statements = [
    env.ATLAS.prepare('INSERT INTO submissions(key,actor,request,response,at) VALUES(?,?,?,?,?)')
      .bind(key, actor, JSON.stringify({ target: FLAGS, input: { finding, glyphs } }), JSON.stringify(out), at),
    // In the order the glyphs were named, as their rows were written before.
    ...flagged.map(({ row }) => env.ATLAS.prepare('INSERT INTO corpus_rows(crop,seq) SELECT ?,coalesce(max(seq),0)+1 FROM corpus_rows').bind(row.id)),
    ...flagged.map(({ glyph, row }) => env.ATLAS.prepare('INSERT INTO corpus_flags(crop,finding,realigned,transcribed,at) VALUES(?,?,?,?,?)')
      .bind(row.id, finding, glyph.realigned, glyph.transcribed, at)),
    ...cropChanges(env, actor, key, at, flagged.map(({ row }) => ({ crop: row.id, expected: row.revision, cut: row.cut, kind: FLAG_KIND,
      label: row.char, state: FLAGGED_STATE, issue: CHARACTER_ISSUE, named: null }))),
  ];
  let results: D1Result[];
  // A round or review may have named a glyph since it was read: its row stops the batch, and the list
  // sent again finds it held.
  try { results = await env.ATLAS.batch(statements) }
  catch (error) {
    if (/UNIQUE|NOT NULL/.test(String(error))) throw new Problem(409, 'A glyph changed while it was being flagged. Send the list again.');
    throw error;
  }
  result.rows_written = results.reduce((n, r) => n + (r.meta?.rows_written ?? 0), 0);
  return result;
}
// Codes 0003 and 0010 number: the event kind of a flag and the wrong-character issue.
export const FLAG_KIND = 6, CHARACTER_ISSUE = 0;

// The corpus glyphs waiting for a person, in the order anything was first written about each (a glyph
// a round named earlier keeps its place when it is flagged later): `crop_corpus_flagged` holds only
// them, so a page reads its own rows however many crops there are.
export const flaggedCorpusQuery = () => `SELECT c.id,f.finding,f.realigned,f.transcribed,f.at FROM crops c INDEXED BY crop_corpus_flagged
  LEFT JOIN corpus_rows r ON r.crop=c.id LEFT JOIN corpus_flags f ON f.crop=c.id
  WHERE c.kind=1 AND c.state=${FLAGGED_STATE} ORDER BY r.seq IS NULL,r.seq,c.id LIMIT ${FLAG_MAX}`;

/** The flagged corpus glyphs as listing items, each flagged by an admin with its finding. */
export async function flaggedCorpus(env: Env, items: (env: Env, ids: number[]) => Promise<Json[]>) {
  const rows = (await env.ATLAS.prepare(flaggedCorpusQuery()).all<Json>()).results;
  const found = new Map((await items(env, rows.map(row => row.id))).map(item => [item.id, item]));
  const listed = rows.flatMap(row => {
    const item = found.get(publicId(row.id));
    if (!item) return [];
    return [row.finding === null ? item : { ...item, flag: { finding: row.finding, realigned: row.realigned, transcribed: row.transcribed, at: iso(row.at) } }];
  });
  // A glyph with no listing item (its record unreadable) is neither shown nor counted.
  return { items: listed, total: listed.length };
}
