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
export const tallyQuery = () => `SELECT char,kind,style,state,quiz,sum(n) AS n FROM tally
  WHERE gen>? AND char IN (SELECT value FROM json_each(?)) GROUP BY char,kind,style,state,quiz`;
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

// A page of one character's collected crops, in the order the index holds them: by style, then
// shuffle and id (`crop_char`), or by witness year, then shuffle and id (`crop_year`, crops with a
// year only). Optional filters ride in the index; withheld crops (`quiz` 0) are left out unless asked
// for. A page continues after the last crop of the one before (`after`), so no page reads the crops
// before it. The partial-index condition is repeated as written, or SQLite reads the table.
export type GalleryQuery = { char: number; style?: number | null; state?: number | null; withheld?: boolean;
  order?: 'style' | 'year'; after?: string | null; limit: number };
export function galleryQuery(q: GalleryQuery): { sql: string; values: number[] } {
  const year = q.order === 'year', lead = year ? 'year' : 'style';
  const where = ['char=?', 'kind=0', year ? 'kind<2 AND year IS NOT NULL' : 'kind<2'], values: number[] = [q.char];
  if (q.style != null) { where.push('style=?'); values.push(q.style); }
  if (q.state != null) { where.push('state=?'); values.push(q.state); }
  if (!q.withheld) where.push('quiz=1');
  const after = q.after ? cursorOf(q.after) : null;
  if (q.after && !after) throw new RangeError('Invalid cursor.');
  if (after) { where.push(`(${lead},shuffle,id)>(?,?,?)`); values.push(...after); }
  return { sql: `SELECT id,${lead} AS lead,shuffle FROM crops WHERE ${where.join(' AND ')} ORDER BY ${lead},shuffle,id LIMIT ?`,
    values: [...values, q.limit + 1] };
}
// A cursor names the last crop shown: its lead key (style code or year), shuffle and public id.
const cursor = (row: { lead: number; shuffle: number; id: number }) => `${row.lead}.${row.shuffle}.${publicId(row.id)}`;
function cursorOf(text: string): [number, number, number] | null {
  const [lead, shuffle, id] = text.split('.');
  const crop = id === undefined ? null : cropOf(id);
  if (crop === null || !/^-?\d{1,10}$/.test(lead) || !/^\d{1,10}$/.test(shuffle)) return null;
  return [Number(lead), Number(shuffle), crop];
}
export async function characterGallery(env: Env, q: GalleryQuery): Promise<{ items: Json[]; next: string | null }> {
  const { sql, values } = galleryQuery(q);
  const rows = (await env.ATLAS.prepare(sql).bind(...values).all<{ id: number; lead: number; shuffle: number }>()).results;
  const page = rows.slice(0, q.limit);
  return { items: await cropItems(env, page.map(row => row.id)), next: rows.length > q.limit ? cursor(page[page.length - 1]) : null };
}
