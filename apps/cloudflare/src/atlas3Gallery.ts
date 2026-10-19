// Character galleries on the new storage layout: counts from the publication's answers plus the
// reviews since, and pages of crops from one range of `crop_char` or `crop_year`.
import { cropItems, cropOf, publicId } from './atlas3';

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
// (`fresh`) carry no decade or visual group, so a count by either stands at the publication.
export type Cell = { kind: number; style: number | null; state: number | null; quiz: number | null; decade: number | null;
  visual_group: number | null; n: number; fresh?: true };
export const SHARDS = 256;
const SHARDS_HELD = 64;
const shards = new Map<string, Promise<Record<string, Json[]>>>();
function shard(env: Env, pub: string, index: number): Promise<Record<string, Json[]>> {
  const key = `v3/pub/${pub}/counts/${index}.json.gz`, hit = shards.get(key);
  if (hit) { shards.delete(key); shards.set(key, hit); return hit; }
  const read = env.MEDIA.get(key).then(async object => {
    if (!object) throw new Error(`${key} is missing`);
    return (await gzipped(object)).chars as Record<string, Json[]>;
  });
  read.catch(() => { if (shards.get(key) === read) shards.delete(key); });
  shards.set(key, read);
  while (shards.size > SHARDS_HELD) shards.delete(shards.keys().next().value!);
  return read;
}
// The tally's key columns write -1 for unknown, since a WITHOUT ROWID key cannot hold NULL.
const known = (value: number) => value === -1 ? null : value;
// One primary-key range per generation since the publication and character, in that order (CROSS JOIN
// keeps it), so no request reads other characters' rows.
export const tallyQuery = () => `WITH RECURSIVE g(n) AS (SELECT ?+1 UNION ALL SELECT n+1 FROM g WHERE n<(SELECT gen FROM generation WHERE id=0))
  SELECT t.char,t.kind,t.style,t.state,t.quiz,sum(t.n) AS n FROM g CROSS JOIN json_each(?) c CROSS JOIN tally t ON t.gen=g.n AND t.char=c.value
  GROUP BY t.char,t.kind,t.style,t.state,t.quiz`;
// Each character's cells: its shard of the answers and its tally rows above the pointer's generation.
export async function characterCounts(env: Env, chars: number[]): Promise<Map<number, Cell[]>> {
  const { pub, through_gen } = await currentPointer(env);
  const wanted = [...new Set(chars)];
  const [read, tally] = await Promise.all([
    Promise.all([...new Set(wanted.map(char => char % SHARDS))].map(index => shard(env, pub, index))),
    env.ATLAS.prepare(tallyQuery()).bind(through_gen, JSON.stringify(wanted))
      .all<{ char: number; kind: number; style: number; state: number; quiz: number; n: number }>(),
  ]);
  const answers = Object.assign({}, ...read) as Record<string, Json[]>;
  const out = new Map<number, Cell[]>(wanted.map(char => [char, (answers[char] ?? []).map(([kind, style, state, quiz, decade, visual_group, n]) =>
    ({ kind, style, state, quiz, decade, visual_group, n }))]));
  for (const row of tally.results)
    out.get(row.char)?.push({ kind: row.kind, style: known(row.style), state: known(row.state), quiz: known(row.quiz),
      decade: null, visual_group: null, n: row.n, fresh: true });
  return out;
}

// A page of one character's collected crops, in the order an index holds them: by style, then shuffle
// and id (`crop_char`), or by witness year, then shuffle and id (`crop_year`), with the crops of no year
// after them by style, shuffle and id (`crop_undated`). Optional filters ride in the index; withheld crops
// (`quiz` 0) are left out unless asked for. A page continues after the last crop of the one before
// (`after`), so no page reads the crops before it. Each partial-index condition is repeated as written,
// or SQLite reads the table.
//
// A style group is a range of style codes, as the live `style_order` groups them: the build gave
// cursive, running, unassessed, mixed and formal the codes 0 to 4, and a code added later (printed,
// seal) counts as formal, as the live CASE's ELSE does. The publisher writes no crop without a style.
export const STYLE_GROUPS = { cursive: [0, 1], unassessed: [2, 3], formal: [4, null] } as const;
export type GalleryQuery = { char: number; group?: keyof typeof STYLE_GROUPS | null; state?: number | null; withheld?: boolean;
  order?: 'style' | 'year'; from?: number | null; to?: number | null; after?: string | null; limit: number };
// The three ranges a gallery reads, and the cursor prefix each writes.
type Phase = 'style' | 'dated' | 'undated';
const PHASES: Record<Phase, { lead: string; prefix: string; where: string }> = {
  style: { lead: 'style', prefix: 's', where: 'kind<2' },
  dated: { lead: 'year', prefix: 'y', where: 'kind<2 AND year IS NOT NULL' },
  undated: { lead: 'style', prefix: 'u', where: 'kind=0 AND year IS NULL' },
};
export function galleryQuery(q: GalleryQuery, phase: Phase, after: [number, number, number] | null): { sql: string; values: number[] } {
  if (!Number.isSafeInteger(q.limit) || q.limit < 1) throw new RangeError('Invalid limit.');
  const { lead, where: partial } = PHASES[phase];
  const where = ['char=?', 'kind=0', partial], values: number[] = [q.char];
  if (q.group) {
    const [low, high] = STYLE_GROUPS[q.group];
    where.push('style>=?'); values.push(low);
    if (high !== null) { where.push('style<=?'); values.push(high); }
  }
  if (q.state != null) { where.push('state=?'); values.push(q.state); }
  if (!q.withheld) where.push('quiz=1');
  if (phase === 'dated' && q.from != null) { where.push('year>=?'); values.push(q.from); }
  if (phase === 'dated' && q.to != null) { where.push('year<=?'); values.push(q.to); }
  if (after) { where.push(`(${lead},shuffle,id)>(?,?,?)`); values.push(...after); }
  return { sql: `SELECT id,${lead} AS lead,shuffle FROM crops WHERE ${where.join(' AND ')} ORDER BY ${lead},shuffle,id LIMIT ?`,
    values: [...values, q.limit + 1] };
}
// A cursor names the range and the last crop shown: its lead key (style code or year), shuffle and
// public id.
type Row = { id: number; lead: number; shuffle: number };
const cursor = (phase: Phase, row: Row) => `${PHASES[phase].prefix}${row.lead}.${row.shuffle}.${publicId(row.id)}`;
export function cursorOf(text: string): { phase: Phase; after: [number, number, number] } {
  const phase = (Object.keys(PHASES) as Phase[]).find(p => PHASES[p].prefix === text[0]);
  const [lead, shuffle, id] = text.slice(1).split('.');
  const crop = id === undefined ? null : cropOf(id);
  if (!phase || crop === null || !/^-?\d{1,10}$/.test(lead) || !/^\d{1,10}$/.test(shuffle)) throw new RangeError('Invalid cursor.');
  return { phase, after: [Number(lead), Number(shuffle), crop] };
}
export async function characterGallery(env: Env, q: GalleryQuery): Promise<{ items: Json[]; next: string | null }> {
  const start = q.after ? cursorOf(q.after) : { phase: (q.order === 'year' ? 'dated' : 'style') as Phase, after: null };
  const read = async (phase: Phase, after: [number, number, number] | null) => {
    const { sql, values } = galleryQuery(q, phase, after);
    return (await env.ATLAS.prepare(sql).bind(...values).all<Row>()).results.map(row => ({ ...row, phase }));
  };
  const rows = await read(start.phase, start.after);
  // Year order runs on into the crops of no year, unless a year range leaves them out.
  if (start.phase === 'dated' && rows.length <= q.limit && q.from == null && q.to == null)
    rows.push(...(await read('undated', null)).slice(0, q.limit + 1 - rows.length));
  const page = rows.slice(0, q.limit), last = page[page.length - 1];
  return { items: await cropItems(env, page.map(row => row.id)), next: rows.length > q.limit ? cursor(last.phase, last) : null };
}
