// The history: who reviewed which crop and who arranged which 構形, newest first, from `events`,
// `submissions` and the ledger's saves in ATLAS, with each reviewer's name from ACCOUNTS (accounts.ts) and each crop's book dates. Crops are public
// ids in the answer; times are ISO strings, as the journal kept them before.
import { publicId } from '../atlas3';
import { actorPeople } from '../accounts';
import { Problem } from '../busy';
import { bookDating } from '../dating';
import { anchoredForm, representationId, typed } from '../representation';

type Json = Record<string, any>;
export type HistoryTools = {
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  integer: (q: URLSearchParams, key: string, fallback: number, max?: number) => number;
};

// A round's label, as `submission_label` indexes it.
const ROUND_LABEL = `iif(json_valid(request), json_extract(request, '$.input.label'), NULL)`;
// Events the history lists: verdicts, and the undos of verdicts. A mark (seen, hard), the undo that
// clears one, and an admin's flag are no person's review. Codes are read with the query, so a code a
// migration adds is seen at once.
const VERDICTS = `(SELECT code FROM codes WHERE field='kind' AND key IN ('match','wrong','unsure'))`;
const UNDO = `(SELECT code FROM codes WHERE field='kind' AND key='undo')`;
const LISTED = `(kind IN ${VERDICTS} OR (kind=${UNDO} AND EXISTS(SELECT 1 FROM events p WHERE p.id=events.reverses AND p.kind IN ${VERDICTS})))`;
// A save of the ledger that arranges 構形 (a form filed, a structure folded), as migration 0021 indexes
// them, and its time as the other rows have it. One that changed nothing (the filing already stood) wrote
// no claim and took none back.
const ARRANGES = `coalesce(json_type(request, '$.structure'), json_type(request, '$.fold')) IS NOT NULL`;
const ARRANGED = `(json_extract(response,'$.replaced[0]') IS NOT NULL OR json_type(response,'$.replaced')='text'
    OR EXISTS(SELECT 1 FROM assertions a WHERE a.asserted_by=l.actor AND a.submission=l.id AND a.legacy IS NULL AND a.predicate<>'represented_by'))`;
const SAVED_AT = `CAST(strftime('%s',at) AS INTEGER)*1000+CAST(substr(at,21,3) AS INTEGER)`;
const iso = (at: number) => new Date(at).toISOString();
// D1 takes at most five terms in a compound SELECT and each id is three arms (its reviews, its passed
// rounds, its arrangements), so a user's ids are read one to a statement, in one batch, each statement
// bounded by the LIMIT, and the statements' pages merged here.
const HISTORY_IDS = 1;

/** Where a page ends: a row's time, whether it is an event (0), a passed round (1) or an arrangement of
 *  構形 (2), and its id. At one time a round comes before its own events, written with it, as the old
 *  journal's ids ordered them. */
export type Cursor = { at: number; src: 0 | 1 | 2; id: number };
export const encodeCursor = (c: Cursor) => btoa(JSON.stringify([c.at, c.src, c.id]));
export function decodeCursor(value: string): Cursor {
  try {
    const d = JSON.parse(atob(value));
    // A page loaded before the cutover holds the old cursor, its last row's time and text id: the list
    // goes on from before that time.
    if (Array.isArray(d) && d.length === 2 && typeof d[0] === 'string' && typeof d[1] === 'string' && Number.isSafeInteger(Date.parse(d[0])))
      return { at: Date.parse(d[0]), src: 0, id: 0 };
    if (!Array.isArray(d) || d.length !== 3 || !d.every(Number.isSafeInteger) || ![0, 1, 2].includes(d[1])) throw 0;
    return { at: d[0], src: d[1], id: d[2] };
  } catch { throw new Problem(422, 'Invalid cursor.') }
}

/**
 * One page of the history, newest first, keyset-paged on (at, src, id). Given `actors`, the rows are
 * those written under any of those ids: one arm per id and kind of row, each read along its index
 * (`event_actor_history`, `submission_actor`, `ledger_structure_actor`), merged by the compound ORDER BY so
 * the read stops at the LIMIT. Given `label`, events read `event_shown`, rounds `submission_label` and
 * arrangements those of its form, by `ledger_structure_subject`. Parameters are
 * numbered so each value is bound once for every arm; the LIMIT is the parameter after `values`.
 */
export function historyQuery(actors: number[] | null, label: { text: string; shown: number | null; form: string | null } | null,
  cursor: Cursor | null): { sql: string; values: (string | number)[] } {
  const values: (string | number)[] = [];
  const bind = (value: string | number) => `?${values.push(value)}`;
  const events = [LISTED];
  const rounds = ['undone=0', 'EXISTS(SELECT 1 FROM seen k WHERE k.submission=submissions.id)'];
  const arrangements = [ARRANGES, ARRANGED];
  if (label) {
    arrangements.push(label.form === null ? '0' : `json_extract(response, '$.subject')=${bind(label.form)}`);
    // A label no grapheme has was shown in no event.
    events.push(label.shown === null ? '0' : `shown=${bind(label.shown)}`);
    rounds.push(`${ROUND_LABEL}=${bind(label.text)}`);
  }
  if (cursor) {
    const at = bind(cursor.at);
    // At one time an arrangement comes first, then a round, then events. After a row: those that follow it.
    events.push(cursor.src === 0 ? `(at,id)<(${at},${bind(cursor.id)})` : `at<=${at}`);
    rounds.push(cursor.src === 0 ? `at<${at}` : cursor.src === 1 ? `(at,id)<(${at},${bind(cursor.id)})` : `at<=${at}`);
    arrangements.push(cursor.src === 2 ? `(at,l.rowid)<(${bind(iso(cursor.at))},${bind(cursor.id)})` : `at<${bind(iso(cursor.at))}`);
  }
  const event = (filter: string[]) => `SELECT 0 AS src,id,at,actor,crop,kind,issue,kept_issue,shown,named,text,reverses,submission,NULL AS passed,NULL AS label,NULL AS save
    FROM events WHERE ${filter.join(' AND ')}`;
  const round = (filter: string[]) => `SELECT 1 AS src,id,at,actor,NULL AS crop,NULL AS kind,NULL AS issue,NULL AS kept_issue,NULL AS shown,NULL AS named,
    NULL AS text,NULL AS reverses,id AS submission,(SELECT count(*) FROM seen k WHERE k.submission=submissions.id) AS passed,
    ${ROUND_LABEL} AS label,NULL AS save FROM submissions WHERE ${filter.join(' AND ')}`;
  const arrangement = (filter: string[]) => `SELECT 2 AS src,l.rowid AS id,${SAVED_AT} AS at,(SELECT p.id FROM actors p WHERE p.key=l.actor) AS actor,NULL AS crop,NULL AS kind,
    NULL AS issue,NULL AS kept_issue,NULL AS shown,NULL AS named,NULL AS text,NULL AS reverses,NULL AS submission,NULL AS passed,NULL AS label,l.id AS save
    FROM ledger_submissions l WHERE ${filter.join(' AND ')}`;
  const arms = actors === null ? [event(events), round(rounds), arrangement(arrangements)]
    : actors.flatMap(actor => { const a = bind(actor);
      return [event([...events, `actor=${a}`]), round([...rounds, `actor=${a}`]), arrangement([...arrangements, `actor=(SELECT p.key FROM actors p WHERE p.id=${a})`])]; });
  // Each row's request is read for its kind (round, batch or one crop) and id, its kind, issue and
  // labels by key, and its crop's book and kind by the ids they carry; an arrangement's save for the
  // form or structure it is about and the structures it names.
  const sql = `SELECT h.*,s.key,json_type(s.request,'$.target') AS target_type,json_extract(s.request,'$.target') AS target,
    json_extract(s.request,'$.input.id') AS request_id,kc.key AS kind_key,ic.key AS issue_key,kept.key AS kept_key,gs.text AS shown_text,
    gn.text AS named_text,pg.book,c.kind AS crop_kind,
    json_type(l.request,'$.fold') IS NOT NULL AS fold,json_extract(l.response,'$.subject') AS subject,fr.value AS form_text,fs.label AS from_label,
    (SELECT st.label FROM assertions a JOIN structures st ON st.id=a.object WHERE a.asserted_by=l.actor AND a.submission=l.id AND a.legacy IS NULL
      AND a.predicate<>'represented_by' AND a.predicate IN ('has_structure','folds_into') LIMIT 1) AS into_label,
    (SELECT st.label FROM structures st WHERE st.id=coalesce(json_extract(l.response,'$.replaced[0]'),json_extract(l.response,'$.replaced'))) AS replaced_label
    FROM (${arms.join(' UNION ALL ')} ORDER BY at DESC,src DESC,id DESC LIMIT ?${values.length + 1}) h
    LEFT JOIN submissions s ON s.id=h.submission LEFT JOIN codes kc ON kc.field='kind' AND kc.code=h.kind
    LEFT JOIN codes ic ON ic.field='issue' AND ic.code=h.issue LEFT JOIN codes kept ON kept.field='issue' AND kept.code=h.kept_issue LEFT JOIN graphemes gs ON gs.id=h.shown
    LEFT JOIN graphemes gn ON gn.id=h.named LEFT JOIN pages pg ON pg.id=h.crop>>24 LEFT JOIN crops c ON c.id=h.crop
    LEFT JOIN ledger_submissions l ON l.id=h.save LEFT JOIN forms fo ON fo.id=json_extract(l.response,'$.subject') LEFT JOIN representations fr ON fr.id=fo.anchor
    LEFT JOIN structures fs ON fs.id=json_extract(l.response,'$.subject') ORDER BY h.at DESC,h.src DESC,h.id DESC`;
  return { sql, values };
}

// A review keeps the words it was saved with. One saved while crops carried a reading names the
// wrong-character issue `reading`.
export const recordedIssue = (issue: unknown) => issue === 'reading' ? 'character' : issue ?? null;

export type HistoryRow = { src: 0 | 1 | 2; id: number; at: number; actor: number | null; crop: number | null; kind: number | null;
  issue: number | null; text: string | null; reverses: number | null; passed: number | null; label: string | null;
  key: string | null; target_type: string | null; target: string | null; request_id: string | null;
  kind_key: string | null; issue_key: string | null; kept_key: string | null; shown_text: string | null; named_text: string | null; book: number | null;
  crop_kind: number | null; fold: number | null; subject: string | null; form_text: string | null; from_label: string | null;
  into_label: string | null; replaced_label: string | null };
export type Reviewer = { user: string | null; name: string | null; image: string | null };

/** A history row as the page shows it. A round's or batch's id is the request's own, as its undo
 *  names it. A passed round is listed by its submission's number, since its key begins with the actor
 *  key, and an old reviewer id is all it takes to claim one. `actor` tells reviewers apart by number. An
 *  arrangement names the form filed (`label`, its id `form`) or the structure folded, and the structure
 *  labels it went between: `into` null for one taken out or split off, `from` null for one first filed. */
export function historyItem(row: HistoryRow, reviewer: Reviewer, me: string | null = null): Json {
  const who = { ...reviewer, mine: Boolean(me && reviewer.user === me) };
  const at = new Date(row.at).toISOString();
  if (row.src === 1) return { id: `r${row.id}`, at, target: null, label: row.label, kind: 'passed', reviewer: who, actor: row.actor,
    passed: Number(row.passed), verdict: null, issue: null, character: null, text: null, round: row.request_id, batch: null, undoes: null };
  if (row.src === 2) {
    const fold = Boolean(row.fold);
    return { id: `a${row.id}`, at, target: null, label: fold ? row.from_label : row.form_text, kind: fold ? 'fold' : 'filing', reviewer: who, actor: row.actor,
      form: fold ? null : row.subject, from: row.replaced_label, into: row.into_label, verdict: null, issue: null, character: null, text: null,
      round: null, batch: null, undoes: null };
  }
  const kind = row.kind_key, undo = kind === 'undo';
  // A round's request has no target, a batch's names '@batch', a single review's names its crop.
  const round = !undo && row.target_type === 'null', batch = !undo && row.target === '@batch';
  return {
    id: String(row.id), at, target: publicId(row.crop!), ...(row.crop_kind === 1 ? { origin: 'corpus' } : {}),
    label: row.shown_text, kind: undo ? 'undo' : 'review',
    reviewer: who, actor: row.actor,
    verdict: undo ? null : kind ?? null,
    issue: undo ? null : recordedIssue(row.issue_key),
    // The issue the crop stays reported for beside the one this review names.
    ...(!undo && row.kept_key ? { also: recordedIssue(row.kept_key) } : {}),
    character: undo ? null : row.named_text,
    text: undo ? null : row.text,
    round: round ? row.request_id : null,
    batch: batch ? row.request_id : null,
    undoes: undo && row.reverses !== null ? String(row.reverses) : null,
  };
}

export async function history(env: Env, q: URLSearchParams, me: string | null, tools: HistoryTools) {
  const limit = Math.max(1, tools.integer(q, 'limit', 40, 100));
  if (q.get('mine') === 'true' && !me) return { items: [], next: null };
  const user = q.get('mine') === 'true' ? me : q.get('user') ? tools.text(q.get('user'), 64, 'user', true) : null;
  const text = q.get('label') ? tools.text(q.get('label'), 32, 'label', true)! : null;
  const before = q.get('before') ? decodeCursor(q.get('before')!) : null;
  const [actors, shown] = await Promise.all([
    user ? env.ATLAS.prepare('SELECT id FROM actors WHERE user=? ORDER BY key').bind(user).all<{ id: number }>().then(r => r.results.map(a => a.id)) : null,
    text === null ? null : env.ATLAS.prepare('SELECT id FROM graphemes WHERE text=?').bind(text).first<{ id: number }>(),
  ]);
  const named = text === null ? null : typed(text);
  const label = text === null ? null : { text, shown: shown?.id ?? null,
    form: named && typeof named !== 'string' ? await anchoredForm(await representationId(named)) : null };
  const groups = actors === null ? [null] : Array.from({ length: Math.ceil(actors.length / HISTORY_IDS) }, (_, i) => actors.slice(i * HISTORY_IDS, (i + 1) * HISTORY_IDS));
  if (!groups.length) return { items: [], next: null };
  const pages = await env.ATLAS.batch<HistoryRow>(groups.map(group => {
    const { sql, values } = historyQuery(group, label, before);
    return env.ATLAS.prepare(sql).bind(...values, limit + 1);
  }));
  const rows = pages.flatMap(page => page.results);
  if (pages.length > 1) rows.sort((a, b) => b.at - a.at || b.src - a.src || b.id - a.id);
  const page = rows.slice(0, limit);
  const [people, dating] = await Promise.all([actorPeople(env, page.map(row => row.actor)), bookDating(env, page.map(row => row.book))]);
  const items = page.map(row => {
    const person = row.actor === null ? undefined : people.get(row.actor);
    // A reviewer id nobody holds is shown with no name: the id is all it takes to claim it.
    const reviewer = { user: person?.user ?? null, name: person?.user ? person.name ?? null : null, image: person?.image ?? null };
    return { ...historyItem(row, reviewer, me), dating: (row.book !== null && dating.get(row.book)) || {} };
  });
  // The cursor is the last row returned; the next page starts strictly after it.
  const last = rows.length > limit ? rows[limit - 1] : null;
  return { items, next: last ? encodeCursor({ at: last.at, src: last.src, id: last.id }) : null };
}
