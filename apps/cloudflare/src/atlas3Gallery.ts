// Character galleries on the new storage layout: counts from the publication's answers plus the
// reviews since, and pages of crops read from index ranges in the order they show.
import { cropOf, publicId } from './atlas3';
import { keyHeads } from './ledger';

type Json = any;

// The publication the answers come from, `v3/pointer.json` (scripts/atlas3/answers.py). An isolate
// reads it again after POINTER_HELD milliseconds, so a new publication shows within a minute.
export type Pointer = { pub: string; through_gen: number };
const POINTER_HELD = 60_000;
let pointer: { at: number; read: Promise<Pointer> } | null = null;
async function gzipped(object: R2ObjectBody): Promise<Json> {
  return await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).json();
}
export function currentPointer(env: Env, now = Date.now()): Promise<Pointer> {
  if (pointer && now - pointer.at < POINTER_HELD) return pointer.read;
  const read = env.MEDIA.get('v3/pointer.json').then(async object => {
    if (!object) throw new Error('v3/pointer.json is missing');
    return await gzipped(object) as Pointer;
  });
  const held = { at: now, read };
  read.catch(() => { if (pointer === held) pointer = null; });
  pointer = held;
  return read;
}

// A count cell: crops of one kind, style, state and withheld flag (`quiz` 0), witness decade and
// visual group, in the codes of `codes`; null is unknown. Cells from the reviews since the publication
// (`fresh`) carry no visual group, so a count by one stands at the publication.
export type Cell = { kind: number; style: number | null; state: number | null; quiz: number | null; decade: number | null;
  visual_group: number | null; n: number; fresh?: true };
export const SHARDS = 256;
const SHARDS_HELD = 64;
type Shard = { chars: Record<string, Json[]>; families: Record<string, Json[]> };
const shards = new Map<string, Promise<Shard>>();
function shard(env: Env, pub: string, index: number): Promise<Shard> {
  const key = `v3/pub/${pub}/counts/${index}.json.gz`, hit = shards.get(key);
  if (hit) { shards.delete(key); shards.set(key, hit); return hit; }
  const read = env.MEDIA.get(key).then(async object => {
    if (!object) throw new Error(`${key} is missing`);
    const body = await gzipped(object);
    return { chars: body.chars ?? {}, families: body.families ?? {} } as Shard;
  });
  read.catch(() => { if (shards.get(key) === read) shards.delete(key); });
  shards.set(key, read);
  while (shards.size > SHARDS_HELD) shards.delete(shards.keys().next().value!);
  return read;
}
const cellOf = ([kind, style, state, quiz, decade, visual_group, n]: Json[]): Cell => ({ kind, style, state, quiz, decade, visual_group, n });
// The tally's key columns write -1 for unknown, since a WITHOUT ROWID key cannot hold NULL.
const known = (value: number) => value === -1 ? null : value;
// One primary-key range per generation since the publication and character, in that order (CROSS JOIN
// keeps it), so no request reads other characters' rows. The crops with no character are the rows of
// character -1, read by family, the next key column.
export const tallyQuery = (by: 'char' | 'family' = 'char') => `WITH RECURSIVE g(n) AS (SELECT ?+1 UNION ALL SELECT n+1 FROM g WHERE n<(SELECT gen FROM generation WHERE id=0))
  SELECT c.value AS k,t.kind,t.style,t.state,t.quiz,t.decade,sum(t.n) AS n FROM g CROSS JOIN json_each(?) c CROSS JOIN tally t
  ON t.gen=g.n AND ${by === 'char' ? 't.char=c.value' : 't.char=-1 AND t.family=c.value'}
  GROUP BY c.value,t.kind,t.style,t.state,t.quiz,t.decade`;
type TallyRow = { k: number; kind: number; style: number; state: number; quiz: number; decade: number; n: number };
// Each key's cells: its shard of the answers (`chars` or `families`) and its tally rows above the
// pointer's generation.
async function counts(env: Env, keys: number[], by: 'char' | 'family'): Promise<Map<number, Cell[]>> {
  const { pub, through_gen } = await currentPointer(env);
  const wanted = [...new Set(keys)];
  if (!wanted.length) return new Map();
  const [read, tally] = await Promise.all([
    Promise.all([...new Set(wanted.map(key => key % SHARDS))].map(index => shard(env, pub, index))),
    env.ATLAS.prepare(tallyQuery(by)).bind(through_gen, JSON.stringify(wanted)).all<TallyRow>(),
  ]);
  const answers = Object.assign({}, ...read.map(s => by === 'char' ? s.chars : s.families)) as Record<string, Json[]>;
  const out = new Map<number, Cell[]>(wanted.map(key => [key, (answers[key] ?? []).map(cellOf)]));
  for (const row of tally.results)
    out.get(row.k)?.push({ kind: row.kind, style: known(row.style), state: known(row.state), quiz: known(row.quiz),
      decade: known(row.decade), visual_group: null, n: row.n, fresh: true });
  return out;
}
/** Each character's cells. */
export const characterCounts = (env: Env, chars: number[]) => counts(env, chars, 'char');
/** The cells of the crops with no character filed under each family (`crops.family`). */
export const filedCounts = (env: Env, families: number[]) => counts(env, families, 'family');

// A style group is a range of style codes, as the live `style_order` groups them: the build gave
// cursive, running, unassessed, mixed and formal the codes 0 to 4, and a code added later (printed,
// seal) counts as formal, as the live CASE's ELSE does. The publisher writes no crop without a style.
export const STYLE_GROUPS = { cursive: [0, 1], unassessed: [2, 3], formal: [4, null] } as const;
export type StyleGroup = keyof typeof STYLE_GROUPS;
export const STYLE_NAMES = Object.keys(STYLE_GROUPS) as StyleGroup[];
export const groupOf = (style: number | null): StyleGroup => style === null || style >= 4 ? 'formal' : style >= 2 ? 'unassessed' : 'cursive';

// Where a gallery's crops are read: the crops of a character (`crop_char` and its kin), the crops with
// no character filed under a family (`crop_family` and its kin), or the crops a grapheme claim files
// under a family from outside it, by id. A family's char and family sources leave out (`except`) the
// crops a claim files under another grapheme. The ids are bound as one JSON list.
export type Source = { by: 'char' | 'family'; key: number; except?: number[] } | { by: 'claimed'; key: number; ids: number[] };
/** A source's conditions in `kind` and their values. A char or family source's are its index's. */
export function sourceWhere(source: Source, kind: 0 | 1): { where: string[]; values: (number | string)[] } {
  if (source.by === 'claimed') return { where: ['id IN (SELECT value FROM json_each(?))', `kind=${kind}`], values: [JSON.stringify(source.ids)] };
  const where = source.by === 'char' ? ['char=?', `kind=${kind}`] : ['family=?', `kind=${kind}`, 'family IS NOT NULL', 'char IS NULL'];
  const values: (number | string)[] = [source.key];
  if (source.except?.length) { where.push('id NOT IN (SELECT value FROM json_each(?))'); values.push(JSON.stringify(source.except)); }
  return { where, values };
}

// The statuses of a grapheme slot that file its crop.
export const FILING = ['asserted', 'accepted', 'adjudicated'];
// At most this many claimed crops are read for a gallery; past it the claims beyond are not applied,
// and the claims want a column of their own on `crops`.
export const FILED_MAX = 2000;
// The crops a grapheme claim files, on each crop's effective cut (the next one while a redrawn box waits
// in `reviews`, as the inspector reads the slot), with what their counts are kept by: one range of
// `current_claim_object` (every crop read as some grapheme) and each crop and review by its key.
export const filedClaimsQuery = () => `SELECT k.id,c.object AS grapheme,k.char,k.family,k.kind,k.book,k.production,k.category,k.shuffle,k.style,k.state,k.quiz,k.visual_group,
  coalesce((k.year-CASE WHEN k.year<0 THEN 9 ELSE 0 END)/10*10,-1) AS decade
  FROM current_claims c CROSS JOIN crops k ON k.id=c.crop LEFT JOIN reviews r ON r.crop=k.id
  WHERE c.predicate='has_grapheme' AND c.object IS NOT NULL AND c.scope='' AND c.slot='' AND c.status IN (${FILING.map(s => `'${s}'`).join(',')})
    AND c.cut=k.cut+(r.x IS NOT NULL) LIMIT ${FILED_MAX}`;
export type Filed = { id: number; grapheme: string; char: number | null; family: number | null; kind: number; book: number; production: number | null; category: number | null; shuffle: number; style: number | null;
  state: number | null; quiz: number | null; visual_group: number | null; decade: number };
/** For a family headed by `grapheme` (its key) with these members: the crops of its members a claim
 *  files elsewhere (`away`), and the crops from outside it a claim files here (`here`). A claim files
 *  its crop under the family its grapheme heads now, or is filed under since a publication moved it. */
export async function familyClaims(env: Env, grapheme: string, members: number[]): Promise<{ away: Filed[]; here: Filed[] }> {
  const rows = await filedClaims(env), own = new Set(members);
  const inFamily = (r: Filed) => r.char !== null ? own.has(r.char) : r.family !== null && own.has(r.family);
  return { away: rows.filter(r => r.head !== grapheme && inFamily(r)), here: rows.filter(r => r.head === grapheme && !inFamily(r)) };
}
/** Every crop a grapheme claim files, with the key of the head of the family it files it under now. */
export async function filedClaims(env: Env): Promise<(Filed & { head: string })[]> {
  const rows = (await env.ATLAS.prepare(filedClaimsQuery()).all<Filed>()).results, heads = await keyHeads(env, rows.map(r => r.grapheme));
  return rows.map(r => ({ ...r, head: heads.get(r.grapheme)! }));
}
/** A filed crop's count cell, `sign` 1 where a claim adds it and -1 where it takes it away. */
export const filedCell = (r: Filed, sign: number): Cell => ({ kind: r.kind, style: r.style, state: r.state, quiz: r.quiz,
  decade: r.decade === -1 ? null : r.decade, visual_group: r.visual_group, n: sign });
export type Years = { from: number; to: number } | 'undated' | null;
export type GalleryQuery = {
  kind: 0 | 1;
  // Listed one after another; the sources of one segment are merged in the gallery's order.
  segments: Source[][];
  group?: StyleGroup | null; state?: number | null;
  // Withheld crops (`quiz` 0) are left out unless asked for.
  withheld?: boolean;
  order?: 'style' | 'year'; years?: Years; axis?: 'witness' | 'composed';
  // Each book with a date on the composed axis, and its year, for `axis` composed.
  composed?: Map<number, number>;
  after?: string | null; limit: number;
};
// The ranges a gallery reads, each with the key it is ordered by after its source: S by style; on the
// witness axis Y by year, U the crops of no year by style, R a year range by style; on the composed
// axis C by the year of the crop's book, K a range of those years by style, N the crops of the other
// books by style. Every range then orders by shuffle and id.
export type Phase = 'S' | 'Y' | 'U' | 'R' | 'C' | 'K' | 'N';
export function phasesOf(q: GalleryQuery): Phase[] {
  const year = q.order === 'year', composed = q.axis === 'composed';
  if (q.years === 'undated') return [composed ? 'N' : 'U'];
  if (q.years) return composed ? [year ? 'C' : 'K'] : [year ? 'Y' : 'R'];
  if (year) return composed ? ['C', 'N'] : ['Y', 'U'];
  return ['S'];
}
// The books a composed range reads: those dated in the years asked for, or all.
export function composedBooks(q: GalleryQuery): [number, number][] {
  const years = q.years && q.years !== 'undated' ? q.years : null;
  return [...(q.composed ?? new Map())].filter(([, y]) => !years || (y >= years.from && y <= years.to)).sort((a, b) => a[0] - b[0]);
}
const caseYear = (books: [number, number][]) => books.length ? `CASE book ${books.map(([b, y]) => `WHEN ${b} THEN ${y}`).join(' ')} END` : 'NULL';

/** The statement for one range of one source, from the cursor `after` (lead, shuffle, id) on. Each
 *  partial-index condition is repeated as written, or SQLite reads the table; `kind` is written as a
 *  literal for the same reason, and the index is named. A range ordered by something its index does not
 *  hold (R, C, K) sorts the source's crops in that range. */
export function rangeQuery(q: GalleryQuery, phase: Phase, source: Source, after: [number, number, number] | null, limit: number) {
  if (!Number.isSafeInteger(limit) || limit < 1) throw new RangeError('Invalid limit.');
  const books = composedBooks(q), list = books.map(([b]) => b).join(',');
  // A composed range with no book in it holds nothing.
  if ((phase === 'C' || phase === 'K') && !books.length) return null;
  const kind = q.kind === 1 ? 1 : 0, char = source.by === 'char', { where, values } = sourceWhere(source, kind);
  const years = q.years && q.years !== 'undated' ? q.years : null;
  let lead = 'style', index = char ? 'crop_char' : 'crop_family';
  if (phase === 'S') { if (char) where.push('kind<2'); }
  else if (phase === 'Y' || phase === 'R') {
    where.push(...(char ? ['kind<2'] : []), 'year IS NOT NULL');
    if (years) { where.push('year>=?', 'year<=?'); values.push(years.from, years.to); }
    index = char ? 'crop_year' : 'crop_family_year';
    if (phase === 'Y') lead = 'year';
  } else if (phase === 'U') {
    if (char) { where.push(`kind=${kind} AND year IS NULL`); index = kind === 0 ? 'crop_undated' : 'crop_undated_corpus'; }
    else where.push('year IS NULL');
  } else if (phase === 'C' || phase === 'K') {
    where.push(...(char ? ['kind<2'] : []), `book IN (${list})`);
    index = char ? 'crop_char_book' : 'crop_family_book';
    if (phase === 'C') lead = caseYear(books);
  } else {
    where.push(...(char ? ['kind<2'] : []));
    if (q.composed?.size) where.push(`+book NOT IN (${[...q.composed.keys()].sort((a, b) => a - b).join(',')})`);
  }
  if (q.group) {
    const [low, high] = STYLE_GROUPS[q.group];
    where.push('style>=?'); values.push(low);
    if (high !== null) { where.push('style<=?'); values.push(high); }
  }
  if (q.state != null) { where.push('state=?'); values.push(q.state); }
  if (!q.withheld) where.push('quiz=1');
  if (after) { where.push(`(${lead},shuffle,id)>(?,?,?)`); values.push(...after); }
  return { sql: `SELECT id,${lead} AS lead,shuffle FROM crops${source.by === 'claimed' ? '' : ` INDEXED BY ${index}`} WHERE ${where.join(' AND ')}`
    + ` ORDER BY ${lead},shuffle,id LIMIT ?`, values: [...values, limit] };
}

// A cursor names the segment and range of the last crop shown, then its lead key, shuffle and public id.
type Row = { id: number; lead: number; shuffle: number };
const cursor = (segment: number, phase: Phase, row: Row) => `${segment}${phase}${row.lead}.${row.shuffle}.${publicId(row.id)}`;
export function cursorOf(text: string): { segment: number; phase: Phase; after: [number, number, number] } {
  const found = /^(\d{1,3})([SYURCKN])(-?\d{1,10})\.(\d{1,10})\.([0-9a-z]{1,11})$/.exec(text);
  const crop = found ? cropOf(found[5]) : null;
  if (!found || crop === null) throw new RangeError('Invalid cursor.');
  return { segment: Number(found[1]), phase: found[2] as Phase, after: [Number(found[3]), Number(found[4]), crop] };
}
const before = (a: Row, b: Row) => a.lead - b.lead || a.shuffle - b.shuffle || a.id - b.id;

/** One page of a gallery: the crops' ids in order, and the cursor of the next page or null. The range
 *  the cursor names is read first; only when it runs out are the ranges after it read, in one batch. */
export async function galleryPage(env: Env, q: GalleryQuery): Promise<{ ids: number[]; next: string | null }> {
  const phases = phasesOf(q), want = q.limit + 1;
  if (!q.segments.length) {
    if (q.after) throw new RangeError('Invalid cursor.');
    return { ids: [], next: null };
  }
  const start = q.after ? cursorOf(q.after) : { segment: 0, phase: phases[0], after: null };
  if (start.segment >= q.segments.length || !phases.includes(start.phase)) throw new RangeError('Invalid cursor.');
  const positions: [number, Phase][] = [];
  for (let s = start.segment; s < q.segments.length; s++)
    for (const phase of phases.slice(s === start.segment ? phases.indexOf(start.phase) : 0)) positions.push([s, phase]);
  const read = async (list: [number, Phase][], first: boolean) => {
    const statements = list.flatMap(([s, phase], i) => q.segments[s].flatMap(source => {
      const range = rangeQuery(q, phase, source, first && i === 0 ? start.after : null, want);
      return range ? [{ at: i, statement: env.ATLAS.prepare(range.sql).bind(...range.values) }] : [];
    }));
    if (!statements.length) return list.map(() => [] as Row[]);
    const results = await env.ATLAS.batch(statements.map(s => s.statement)) as D1Result<Row>[];
    const byPosition = list.map(() => [] as Row[]);
    results.forEach((result, k) => byPosition[statements[k].at].push(...result.results));
    return byPosition.map(rows => rows.sort(before).slice(0, want));
  };
  const rows: (Row & { at: [number, Phase] })[] = [];
  const take = (list: [number, Phase][], found: Row[][]) => {
    for (let i = 0; i < list.length && rows.length < want; i++)
      for (const row of found[i].slice(0, want - rows.length)) rows.push({ ...row, at: list[i] });
  };
  take(positions.slice(0, 1), await read(positions.slice(0, 1), true));
  if (rows.length < want && positions.length > 1) take(positions.slice(1), await read(positions.slice(1), false));
  const page = rows.slice(0, q.limit), last = page[page.length - 1];
  return { ids: page.map(row => row.id), next: rows.length > q.limit ? cursor(last.at[0], last.at[1], last) : null };
}

/** How many crops each style group holds in a year range, which the counts cannot answer, on either
 *  axis: one count per source, over the index the range is read from. */
export function rangeCountQuery(q: GalleryQuery, phase: 'R' | 'K', source: Source) {
  const range = rangeQuery({ ...q, group: null }, phase, source, null, 1);
  if (!range) return null;
  const from = range.sql.slice(range.sql.indexOf(' FROM '), range.sql.lastIndexOf(' ORDER BY '));
  return { sql: `SELECT style,count(*) AS n${from} GROUP BY style`, values: range.values.slice(0, -1) };
}
