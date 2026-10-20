// The review history: who reviewed which crop, newest first, from `events` and `submissions` in ATLAS,
// with each reviewer's name from ACCOUNTS (accounts.ts) and each crop's book dates. Crops are public
// ids in the answer; times are ISO strings, as the journal kept them before.
import { publicId } from '../atlas3';
import { actorPeople } from '../accounts';
import { Problem } from '../busy';
import { bookDating } from '../dating';

type Json = Record<string, any>;
export type HistoryTools = {
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  integer: (q: URLSearchParams, key: string, fallback: number, max?: number) => number;
};

// A round's label, as `submission_label` indexes it.
const ROUND_LABEL = `iif(json_valid(request), json_extract(request, '$.input.label'), NULL)`;
// Events the history lists: verdicts and undos. A mark (seen, hard) or an admin's flag is no person's review.
const LISTED = ['match', 'wrong', 'unsure', 'undo'];
// D1 takes at most five terms in a compound SELECT and each id is two arms (its reviews, its passed
// rounds), so a user holding more ids than that is read two ids to a statement, in one batch, each
// statement bounded by the LIMIT, and the statements' pages merged here.
const HISTORY_IDS = 2;

/** Where a page ends: a row's time, whether it is an event (0) or a passed round (1), and its id. At
 *  one time a round comes before its own events, written with it, as the old journal's ids ordered them. */
export type Cursor = { at: number; src: 0 | 1; id: number };
export const encodeCursor = (c: Cursor) => btoa(JSON.stringify([c.at, c.src, c.id]));
export function decodeCursor(value: string): Cursor {
  try {
    const d = JSON.parse(atob(value));
    if (!Array.isArray(d) || d.length !== 3 || !d.every(Number.isSafeInteger) || (d[1] !== 0 && d[1] !== 1)) throw 0;
    return { at: d[0], src: d[1], id: d[2] };
  } catch { throw new Problem(422, 'Invalid cursor.') }
}

/**
 * One page of the history, newest first, keyset-paged on (at, src, id). Given `actors`, the rows are
 * those written under any of those ids: one arm per id and kind of row, each read along its index
 * (`event_actor_history`, `submission_actor`), merged by the compound ORDER BY so the read stops at the
 * LIMIT. Given `shown`, events read `event_shown` and rounds `submission_label`. `kinds` are the event
 * kind codes listed. Parameters are numbered so each value is bound once for every arm; the LIMIT is
 * the parameter after `values`.
 */
export function historyQuery(actors: number[] | null, label: { text: string; shown: number | null } | null,
  cursor: Cursor | null, kinds: number[]): { sql: string; values: (string | number)[] } {
  const values: (string | number)[] = [];
  const bind = (value: string | number) => `?${values.push(value)}`;
  const listed = `kind IN (${kinds.map(Number).join(',')})`;
  const events = [listed];
  const rounds = ['undone=0', 'EXISTS(SELECT 1 FROM seen k WHERE k.submission=submissions.id)'];
  if (label) {
    // A label no grapheme has was shown in no event.
    events.push(label.shown === null ? '0' : `shown=${bind(label.shown)}`);
    rounds.push(`${ROUND_LABEL}=${bind(label.text)}`);
  }
  if (cursor) {
    const at = bind(cursor.at);
    // After an event: older events and older rounds. After a round: events at its time or older, and older rounds.
    events.push(cursor.src === 0 ? `(at,id)<(${at},${bind(cursor.id)})` : `at<=${at}`);
    rounds.push(cursor.src === 0 ? `at<${at}` : `(at,id)<(${at},${bind(cursor.id)})`);
  }
  const event = (filter: string[]) => `SELECT 0 AS src,id,at,actor,crop,kind,issue,shown,named,text,reverses,submission,NULL AS passed,NULL AS label
    FROM events WHERE ${filter.join(' AND ')}`;
  const round = (filter: string[]) => `SELECT 1 AS src,id,at,actor,NULL AS crop,NULL AS kind,NULL AS issue,NULL AS shown,NULL AS named,
    NULL AS text,NULL AS reverses,id AS submission,(SELECT count(*) FROM seen k WHERE k.submission=submissions.id) AS passed,
    ${ROUND_LABEL} AS label FROM submissions WHERE ${filter.join(' AND ')}`;
  const arms = actors === null ? [event(events), round(rounds)]
    : actors.flatMap(actor => { const a = bind(actor); return [event([...events, `actor=${a}`]), round([...rounds, `actor=${a}`])]; });
  // Each row's request is read for its kind (round, batch or one crop) and id, its labels by key, and its
  // crop's book by the page the crop id names.
  const sql = `SELECT h.*,s.key,json_type(s.request,'$.target') AS target_type,json_extract(s.request,'$.target') AS target,
    json_extract(s.request,'$.input.id') AS request_id,gs.text AS shown_text,gn.text AS named_text,pg.book
    FROM (${arms.join(' UNION ALL ')} ORDER BY at DESC,src DESC,id DESC LIMIT ?${values.length + 1}) h
    LEFT JOIN submissions s ON s.id=h.submission LEFT JOIN graphemes gs ON gs.id=h.shown LEFT JOIN graphemes gn ON gn.id=h.named
    LEFT JOIN pages pg ON pg.id=h.crop>>24 ORDER BY h.at DESC,h.src DESC,h.id DESC`;
  return { sql, values };
}

// A review keeps the words it was saved with. One saved while crops carried a reading names the
// wrong-character issue `reading`.
export const recordedIssue = (issue: unknown) => issue === 'reading' ? 'character' : issue ?? null;

export type HistoryRow = { src: 0 | 1; id: number; at: number; actor: number; crop: number | null; kind: number | null;
  issue: number | null; text: string | null; reverses: number | null; passed: number | null; label: string | null;
  key: string | null; target_type: string | null; target: string | null; request_id: string | null;
  shown_text: string | null; named_text: string | null; book: number | null };
export type Reviewer = { user: string | null; name: string; image: string | null };

/** A history row as the page shows it. A round's or batch's id is the request's own, as its undo
 *  names it; a passed round is named by its submission key. */
export function historyItem(row: HistoryRow, codes: { kind: Map<number, string>; issue: Map<number, string> }, reviewer: Reviewer,
  me: string | null = null): Json {
  const who = { ...reviewer, mine: Boolean(me && reviewer.user === me) };
  const at = new Date(row.at).toISOString();
  if (row.src === 1) return { id: row.key, at, target: null, label: row.label, kind: 'passed', reviewer: who,
    passed: Number(row.passed), verdict: null, issue: null, character: null, text: null, round: row.key, batch: null, undoes: null };
  const kind = codes.kind.get(row.kind!), undo = kind === 'undo';
  // A round's request has no target, a batch's names '@batch', a single review's names its crop.
  const round = !undo && row.target_type === 'null', batch = !undo && row.target === '@batch';
  return {
    id: String(row.id), at, target: publicId(row.crop!), label: row.shown_text, kind: undo ? 'undo' : 'review',
    reviewer: who,
    verdict: undo ? null : kind ?? null,
    issue: undo ? null : recordedIssue(row.issue === null ? null : codes.issue.get(row.issue)),
    character: undo ? null : row.named_text,
    text: undo ? null : row.text,
    round: round ? row.request_id : null,
    batch: batch ? row.request_id : null,
    undoes: undo && row.reverses !== null ? String(row.reverses) : null,
  };
}

let held: Promise<{ kind: Map<number, string>; issue: Map<number, string> }> | null = null;
/** The event kind and issue codes, the same until a migration adds one; a failed read is not kept. */
function eventCodes(env: Env) {
  if (!held) {
    const read = env.ATLAS.prepare(`SELECT field,code,key FROM codes WHERE field IN ('kind','issue')`).all<{ field: string; code: number; key: string }>()
      .then(r => ({ kind: new Map(r.results.filter(c => c.field === 'kind').map(c => [c.code, c.key])),
        issue: new Map(r.results.filter(c => c.field === 'issue').map(c => [c.code, c.key])) }));
    read.catch(() => { if (held === read) held = null; });
    held = read;
  }
  return held;
}

export async function history(env: Env, q: URLSearchParams, me: string | null, tools: HistoryTools) {
  const limit = Math.max(1, tools.integer(q, 'limit', 40, 100));
  if (q.get('mine') === 'true' && !me) return { items: [], next: null };
  const user = q.get('mine') === 'true' ? me : q.get('user') ? tools.text(q.get('user'), 64, 'user', true) : null;
  const text = q.get('label') ? tools.text(q.get('label'), 32, 'label', true)! : null;
  const before = q.get('before') ? decodeCursor(q.get('before')!) : null;
  const [codes, actors, shown] = await Promise.all([
    eventCodes(env),
    user ? env.ATLAS.prepare('SELECT id FROM actors WHERE user=? ORDER BY key').bind(user).all<{ id: number }>().then(r => r.results.map(a => a.id)) : null,
    text === null ? null : env.ATLAS.prepare('SELECT id FROM graphemes WHERE text=?').bind(text).first<{ id: number }>(),
  ]);
  const kinds = [...codes.kind].filter(([, key]) => LISTED.includes(key)).map(([code]) => code);
  const label = text === null ? null : { text, shown: shown?.id ?? null };
  const groups = actors === null ? [null] : Array.from({ length: Math.ceil(actors.length / HISTORY_IDS) }, (_, i) => actors.slice(i * HISTORY_IDS, (i + 1) * HISTORY_IDS));
  if (!groups.length) return { items: [], next: null };
  const pages = await env.ATLAS.batch<HistoryRow>(groups.map(group => {
    const { sql, values } = historyQuery(group, label, before, kinds);
    return env.ATLAS.prepare(sql).bind(...values, limit + 1);
  }));
  const rows = pages.flatMap(page => page.results);
  if (pages.length > 1) rows.sort((a, b) => b.at - a.at || b.src - a.src || b.id - a.id);
  const page = rows.slice(0, limit);
  const [people, dating] = await Promise.all([actorPeople(env, page.map(row => row.actor)), bookDating(env, page.map(row => row.book))]);
  const items = page.map(row => {
    const person = people.get(row.actor);
    // A reviewer id nobody holds is shown by itself.
    const reviewer = { user: person?.user ?? null, name: person?.name ?? person?.key ?? String(row.actor), image: person?.image ?? null };
    return { ...historyItem(row, codes, reviewer, me), dating: (row.book !== null && dating.get(row.book)) || {} };
  });
  // The cursor is the last row returned; the next page starts strictly after it.
  const last = rows.length > limit ? rows[limit - 1] : null;
  return { items, next: last ? encodeCursor({ at: last.at, src: last.src, id: last.id }) : null };
}
