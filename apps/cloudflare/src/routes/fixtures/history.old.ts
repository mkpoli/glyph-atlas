// The history routes as origin/main answered them before the port, kept verbatim as a fixture for the
// old-vs-new equivalence tests. Only the helpers they call are added around them.
import { Problem } from '../../busy';
import { withDating } from '../../dating';
import { literal } from '../../index';
type Json = Record<string, any>;
const parse = (value: string): Json => JSON.parse(value);
function chunks<T>(list: T[], size: number): T[][] { const out: T[][] = []; for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size)); return out }
function text(value: unknown, max: number, name: string, required = false): string | null {
  if (value == null && !required) return null;
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw new Problem(422, `Invalid ${name}.`);
  return value.trim();
}
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
// The journal ids of one reviewer: a user's, or one id from before accounts that nobody holds.
export function reviewerActors(input:Json):[string,string[]]{
  if(typeof input.user==='string')return ['(SELECT actor FROM actors WHERE user_id=?)',[text(input.user,64,'user',true)!]];
  if(typeof input.actor==='string')return ['(?)',[text(input.actor,128,'actor',true)!]];
  throw new Problem(422,'Name a user or a reviewer id.');
}
// A review's label sits in evidence.label (a round) or evidence.snapshot.character.label (a single
// correction); evidence is itself a JSON string, parsed once. An undo's own evidence is the plain string
// 'undo of <event id>', not JSON, so its label comes from the row's own snapshot column instead — the
// CASE only evaluates the branch for the row's own kind, so a future kind touches neither column.
// This text is repeated verbatim in migration 0011's expression index; keep the two in sync.
export const historyLabelExpr = () => `(CASE kind WHEN 'review' THEN coalesce(json_extract(json_extract(event,'$.evidence'),'$.label'),json_extract(json_extract(event,'$.evidence'),'$.snapshot.character.label')) WHEN 'undo' THEN json_extract(snapshot,'$.character.label') END)`;
// Newest first, keyset-paged on (at,id): `before` is strictly older than that pair, in index order.
// Given `actors`, the rows are those written under any of those ids: one arm per id, each read in
// event_actor_history order, merged by the compound ORDER BY so the read stops at the LIMIT instead of
// sorting everything the ids ever wrote. Each row names the user who holds its id now, or none for a
// reviewer id from before accounts that nobody has claimed. Parameters are numbered so the label and
// cursor are bound once for every arm; the LIMIT is the parameter after `values`.
export function historyQuery(actors: string[] | null, label: string | null, cursor: { at: string; id: string } | null): { sql: string; values: (string | number)[] } {
  const values: (string | number)[] = [];
  const bind = (value: string) => `?${values.push(value)}`;
  const where = [`kind IN ('review','undo')`];
  if (label !== null) where.push(`${historyLabelExpr()}=${bind(label)}`);
  if (cursor) where.push(`(at,id)<(${bind(cursor.at)},${bind(cursor.id)})`);
  const select = (filter: string[]) => `SELECT id,at,actor,target,kind,event,${historyLabelExpr()} AS label FROM events WHERE ${filter.join(' AND ')}`;
  // A standing round is listed once more for the crops it passed: a pass is not a review of the crop,
  // so it is its own row, with how many crops it passed and the round's character.
  const passedWhere = ['undone=0', `EXISTS(SELECT 1 FROM seen k WHERE k.submission=submissions.id)`];
  if (label !== null) passedWhere.push(`json_extract(request,'$.input.label')=${bind(label)}`);
  if (cursor) passedWhere.push(`(at,id)<(${bind(cursor.at)},${bind(cursor.id)})`);
  const passed = (filter: string[]) => `SELECT id,at,actor,NULL AS target,'passed' AS kind,
    (SELECT count(*) FROM seen k WHERE k.submission=submissions.id) AS event,json_extract(request,'$.input.label') AS label
    FROM submissions WHERE ${filter.join(' AND ')}`;
  const arms = actors === null ? [select(where), passed(passedWhere)]
    : actors.flatMap(actor => [select([...where, `actor=${bind(actor)}`]), passed([...passedWhere, `actor=${bind(actor)}`])]);
  const sql = `SELECT h.*,a.user_id AS user,u.name AS name,u.image AS image FROM (${arms.join(' UNION ALL ')}
    ORDER BY at DESC,id DESC LIMIT ?${values.length + 1}) h
    LEFT JOIN actors a ON a.actor=h.actor LEFT JOIN "user" u ON u.id=a.user_id ORDER BY h.at DESC,h.id DESC`;
  return { sql, values };
}
// D1 takes at most five terms in a compound SELECT and each id is two arms (its reviews, its passed
// rounds), so a user holding more ids than that is read two ids to a statement, in one batch, each
// statement bounded by the LIMIT, and the statements' pages merged here.
const HISTORY_IDS = 2;
export function encodeCursor(at: string, id: string): string {
  return btoa(JSON.stringify([at, id]));
}
export function decodeCursor(value: string): { at: string; id: string } {
  try {
    const decoded = JSON.parse(atob(value));
    if (!Array.isArray(decoded) || decoded.length !== 2 || typeof decoded[0] !== 'string' || typeof decoded[1] !== 'string') throw 0;
    return { at: decoded[0], id: decoded[1] };
  } catch { throw new Problem(422, 'Invalid cursor.') }
}
// A review keeps the words it was saved with. One saved while crops carried a reading names the
// wrong-character issue `reading` and its typed characters `suggested_reading`.
export const recordedIssue = (issue: unknown) => issue === 'reading' ? 'character' : issue ?? null;
export const typedText = (evidence: Json | null | undefined): string | null =>
  evidence?.suggested_text ?? evidence?.suggested_reading ?? null;
type HistoryRow = { id: string; at: string; actor: string; target: string; kind: string; event: string; label: string | null;
  user: string | null; name: string | null; image: string | null };
// A review's evidence names its own verdict, issue and correction; an undo's evidence is only the id
// of the event it reverses, so those fields stay null and `undoes` names that event instead.
export function historyItem(row: HistoryRow, me: string | null = null): Json {
  const reviewer = { user: row.user, name: row.name ?? row.actor, image: row.image, mine: Boolean(me && row.user === me) };
  if (row.kind === 'passed') return { id: row.id, at: row.at, target: null, label: row.label, kind: 'passed', reviewer,
    passed: Number(row.event), verdict: null, issue: null, character: null, text: null, round: row.id, batch: null, undoes: null };
  const undo = row.kind === 'undo';
  const parsedEvent = parse(row.event);
  const evidence = undo ? null : parse(parsedEvent.evidence);
  return {
    id: row.id, at: row.at, target: row.target, label: row.label, kind: row.kind as 'review' | 'undo',
    reviewer,
    verdict: evidence?.verdict ?? null,
    issue: recordedIssue(evidence?.issue),
    character: evidence?.suggested_character ? literal(evidence.suggested_character) : null,
    text: typedText(evidence),
    round: evidence?.round ?? null,
    batch: evidence?.batch ?? null,
    undoes: undo ? String(parsedEvent.evidence).replace(/^undo of /, '') : null,
  };
}
// Each decision with the dates of the book its crop comes from: a local crop's row names the book, a
// named corpus glyph's record its source.
export const historyDocumentsQuery = (n: number) => `SELECT id,coalesce(document,json_extract(data,'$.source.document_id')) AS document
  FROM units WHERE id IN (${Array(n).fill('?').join(',')})`;
async function historyDating(env: Env, items: Json[]): Promise<Json[]> {
  const targets = [...new Set(items.map(item => item.target).filter(Boolean))] as string[];
  if (!targets.length) return items;
  const documents = new Map<string, string>();
  for (const part of chunks(targets, 90))
    for (const row of (await env.DB.prepare(historyDocumentsQuery(part.length)).bind(...part).all<{ id: string; document: string | null }>()).results)
      if (row.document) documents.set(row.id, row.document);
  return withDating(env, items, item => documents.get(item.target) ?? null);
}
export async function history(env: Env, q: URLSearchParams, me: string | null) {
  const limit = Math.max(1, integer(q, 'limit', 40, 100));
  if (q.get('mine') === 'true' && !me) return { items: [], next: null };
  const user = q.get('mine') === 'true' ? me : q.get('user') ? text(q.get('user'), 64, 'user', true) : null;
  const label = q.get('label') ? text(q.get('label'), 32, 'label', true) : null;
  const before = q.get('before') ? decodeCursor(q.get('before')!) : null;
  const actors = user ? (await env.DB.prepare('SELECT actor FROM actors WHERE user_id=? ORDER BY actor').bind(user).all<{ actor: string }>()).results.map(r => r.actor) : null;
  const groups = actors === null ? [null] : Array.from({ length: Math.ceil(actors.length / HISTORY_IDS) }, (_, i) => actors.slice(i * HISTORY_IDS, (i + 1) * HISTORY_IDS));
  const pages = await env.DB.batch<HistoryRow>(groups.map(group => {
    const { sql, values } = historyQuery(group, label, before);
    return env.DB.prepare(sql).bind(...values, limit + 1);
  }));
  const rows = pages.flatMap(page => page.results);
  if (pages.length > 1) rows.sort((a, b) => a.at === b.at ? (a.id < b.id ? 1 : a.id > b.id ? -1 : 0) : a.at < b.at ? 1 : -1);
  const items = await historyDating(env, rows.slice(0, limit).map(row => historyItem(row, me)));
  // The cursor is the last row returned; the next page starts strictly after it.
  const next = rows.length > limit ? encodeCursor(rows[limit - 1].at, rows[limit - 1].id) : null;
  return { items, next };
}
