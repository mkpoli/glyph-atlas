// A publication's answers beyond the per-character counts (scripts/atlas3/answers.py): the browse page's
// counts by character and book, the corpus counts by character and material, each book's crops and
// glyphs, and the homepage sample. A count is the answer plus the `tally` rows above the pointer's
// generation, so a review shows at once.
import { codeNames } from './atlas3';
import { currentPointer } from './atlas3Gallery';
import { categoryOf } from './category';

type Json = any;

/** A text's code points as the site keys a grapheme: `U+306F U+3099`. */
export const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');

// Answers are immutable under their publication, so an isolate keeps the last few it read: the counts
// (a few hundred kilobytes each) apart from the sample's shards, so a run of homepage visits never
// pushes the counts out.
const COUNTS_HELD = 8, SHARDS_HELD = 48;
const counts = new Map<string, Promise<Json>>(), shards = new Map<string, Promise<Json>>();
export function answer(env: Env, pub: string, name: string, answers = counts, kept = COUNTS_HELD): Promise<Json> {
  const key = `v3/pub/${pub}/${name}`, hit = answers.get(key);
  if (hit) { answers.delete(key); answers.set(key, hit); return hit; }
  const read = env.MEDIA.get(key).then(async object => {
    if (!object) throw new Error(`${key} is missing`);
    return await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).json();
  });
  read.catch(() => { if (answers.get(key) === read) answers.delete(key); });
  answers.set(key, read);
  while (answers.size > kept) answers.delete(answers.keys().next().value!);
  return read;
}

// A character's text and the key of the grapheme it is filed under.
export type CharInfo = { text: string; grapheme: string };
// The characters the answers do not name (one a review gave its first crop since the publication): one
// primary-key read each, with its family head.
export const charsQuery = () => `SELECT g.id,g.text,coalesce(h.text,g.text) AS head FROM json_each(?) j
  JOIN graphemes g ON g.id=j.value LEFT JOIN graphemes h ON h.id=g.head`;
export async function charsOf(env: Env, known: Record<string, [string, string]>, ids: Iterable<number>): Promise<Map<number, CharInfo>> {
  const out = new Map<number, CharInfo>(), missing: number[] = [];
  for (const id of new Set(ids)) {
    if (id === -1) continue;
    const found = known[id];
    if (found) out.set(id, { text: found[0], grapheme: found[1] });
    else missing.push(id);
  }
  if (missing.length) for (const row of (await env.ATLAS.prepare(charsQuery()).bind(JSON.stringify(missing))
    .all<{ id: number; text: string; head: string }>()).results) out.set(row.id, { text: row.text, grapheme: codePoints(row.head) });
  return out;
}

// The tally's deltas above a generation, one primary-key range (`gen` leads the key).
export const tallyDeltasQuery = (kind: 0 | 1) => `SELECT char,book,production,state,quiz,sum(n) AS n FROM tally
  WHERE gen>? AND kind=${kind} GROUP BY char,book,production,state,quiz`;
type Delta = { char: number; book: number; production: number; state: number; quiz: number; n: number };

/** A collected crop count: character, book, production and category (-1 unknown; a review's delta has
 *  no category, `null`), state code and withheld flag (`quiz` 0). */
export type Facet = { char: number; book: number; production: number; category: number | null; state: number; quiz: number; n: number };
/** The collected crops by character, book, production, category, state and withheld flag, with the
 *  characters they name, as they stand now. */
export async function collectedCounts(env: Env): Promise<{ pub: string; cells: Facet[]; chars: Map<number, CharInfo> }> {
  const { pub, through_gen } = await currentPointer(env);
  const [body, deltas, names] = await Promise.all([answer(env, pub, 'facets.json.gz'),
    env.ATLAS.prepare(tallyDeltasQuery(0)).bind(through_gen).all<Delta>(), codeNames(env)]);
  const categories = new Map([...names].filter(([key]) => key.startsWith('category:')).map(([key, name]) => [name, Number(key.slice(9))]));
  const cells: Facet[] = (body.cells as number[][]).map(([char, book, production, category, state, quiz, n]) =>
    ({ char, book, production, category, state, quiz, n }));
  const chars = await charsOf(env, body.chars, [...cells.map(c => c.char), ...deltas.results.map(d => d.char)]);
  for (const d of deltas.results) {
    const text = chars.get(d.char)?.text;
    cells.push({ ...d, category: text ? categories.get(categoryOf(text)) ?? null : null });
  }
  return { pub, cells, chars };
}

/** A corpus glyph count: character, production (-1 unknown), state code and withheld flag. */
export type GlyphCell = { char: number; production: number; state: number; quiz: number; n: number };
/** The corpus glyphs with a character by character, production, state and withheld flag. `fresh` adds
 *  the reviews since the publication. */
export async function corpusCounts(env: Env, fresh: boolean): Promise<{ pub: string; cells: GlyphCell[]; chars: Map<number, CharInfo> }> {
  const { pub, through_gen } = await currentPointer(env);
  const [body, deltas] = await Promise.all([answer(env, pub, 'corpus.json.gz'),
    fresh ? env.ATLAS.prepare(tallyDeltasQuery(1)).bind(through_gen).all<Delta>().then(r => r.results) : []]);
  const cells: GlyphCell[] = (body.cells as number[][]).map(([char, production, state, quiz, n]) => ({ char, production, state, quiz, n }));
  for (const d of deltas) if (d.char !== -1) cells.push({ char: d.char, production: d.production, state: d.state, quiz: d.quiz, n: d.n });
  return { pub, cells, chars: await charsOf(env, body.chars, cells.map(c => c.char)) };
}

/** Each book's collected crops and corpus glyphs at the publication, by book id. */
export async function bookCounts(env: Env): Promise<{ pub: string; books: Map<number, [number, number]> }> {
  const { pub } = await currentPointer(env);
  const body = await answer(env, pub, 'books.json.gz');
  return { pub, books: new Map(Object.entries(body.books as Record<string, [number, number]>).map(([book, n]) => [Number(book), n])) };
}

// Productions by id, a few rows the isolate keeps; a count that names one it does not hold reads them
// again, once. -1 is unknown.
let productions: Promise<Map<number, string>> | null = null;
export async function productionKeys(env: Env, ids: Iterable<number> = []): Promise<Map<number, string>> {
  const read = () => {
    const pending = env.ATLAS.prepare('SELECT id,key FROM productions').all<{ id: number; key: string }>()
      .then(r => new Map<number, string>([[-1, 'unknown'], ...r.results.map(row => [row.id, row.key] as [number, string])]));
    pending.catch(() => { if (productions === pending) productions = null; });
    return productions = pending;
  };
  let keys = await (productions ?? read());
  if ([...ids].some(id => !keys.has(id))) keys = await read();
  return keys;
}

// The homepage sample: `index.json.gz` names each pool's shuffle range and shards; shard k holds the
// glyphs from k * range / shards up, by shuffle and id, each [crop, shuffle, category, record].
export type Pool = { range: number; shards: number; glyphs: number; categories: Record<string, number> };
export type SampleEntry = [number, number, string, Json];
export async function galleryIndex(env: Env, pub: string): Promise<Record<string, Pool>> {
  return (await answer(env, pub, 'gallery/index.json.gz')).pools;
}
export async function galleryShard(env: Env, pub: string, pool: string, shard: number): Promise<SampleEntry[]> {
  return (await answer(env, pub, `gallery/${pool}/${shard}.json.gz`, shards, SHARDS_HELD)).entries;
}
