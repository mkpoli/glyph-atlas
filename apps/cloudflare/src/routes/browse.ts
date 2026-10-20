// The catalogue, the homepage and the site's status on the new storage layout (decision record 0005):
// `/atlas` (the browse page, the review rounds and the Flagged view), `/atlas/corpus/characters`,
// `/layers/gallery`, `/atlas/dates/stats`, `/atlas/collection/status`, `/layers/summary` and
// `/health`. Counts come from the publication's answers plus the reviews since (`atlas3Answers.ts`);
// listings deal crops in shuffle order from the indexes of migration 0011.
import { codeNames, publicId, type Entry } from '../atlas3';
import { bookCounts, codePoints, collectedCounts, corpusCounts, galleryIndex, galleryShard, productionKeys, type SampleEntry } from '../atlas3Answers';
import { currentPointer } from '../atlas3Gallery';
import { listingItems, marksOf, suspectOf } from '../atlas3Listing';
import { catalogueKey, catalogueStamps, publicationKey } from '../atlas3Stamps';
import { Problem } from '../busy';
import { categoryOf } from '../category';
import { tallyDates } from '../dating';
import { compose, graphemeKey, graphemeText, integer, literal, text } from '../index';
import { ROUND_MAX } from '../rounds';

type Json = any;

// ---- Values a publication states about the whole site (`site`, migration 0005) and the pointer.

export const siteQuery = () => 'SELECT value FROM site WHERE key=?';
export async function siteValue(env: Env, key: string): Promise<Json> {
  const row = await env.ATLAS.prepare(siteQuery()).bind(key).first<{ value: string }>();
  return row ? JSON.parse(row.value) : null;
}

// A publication is named by the second it was made (`20261019T130411Z`); `published_at` writes it as an
// ISO 8601 time.
export const publishedAt = (pub: string) =>
  pub.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/, '$1-$2-$3T$4:$5:$6+00:00');
// The pointer is read straight, so a site with no publication yet answers null rather than failing.
export async function health(env: Env): Promise<Json> {
  const object = await env.MEDIA.get('v3/pointer.json');
  if (!object) return { ok: true, published_at: null };
  const { pub } = await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).json() as { pub: string };
  return { ok: true, published_at: publishedAt(pub) };
}

// ---- Material scopes. A scope is `all`, a node of the production tree (everything under it too), or
// `not:` a node. Productions are integer ids; -1 is unknown.

// A production is a node of the tree in `data/vocab/production.yaml`, written as its path
// (`printed/type/wood`); `tests/test_production.py` holds this list to the file.
export const PRODUCTIONS = ['handwritten','inscribed','inscribed/stone','inscribed/metal','inscribed/bone','inscribed/wood','printed','printed/woodblock','printed/type','printed/type/wood','printed/type/ceramic','printed/type/metal','printed/type/metal/copper','printed/type/metal/iron','printed/type/metal/lead','printed/engraved','printed/lithograph','printed/stencil','printed/phototype','printed/digital','typewritten','mixed','unknown'];
export const validScope = (scope: string) => scope === 'all' || PRODUCTIONS.includes(scope.replace(/^not:/, ''));
const within = (value: string, node: string) => value === node || value.startsWith(node + '/');
export const inScope = (scope: string, key: string) =>
  scope === 'all' || (scope.startsWith('not:') ? !within(key, scope.slice(4)) : within(key, scope));
// The production ids in a scope, or null for every one.
function scopeIds(scope: string, keys: Map<number, string>): Set<number> | null {
  return scope === 'all' ? null : new Set([...keys].filter(([, key]) => inScope(scope, key)).map(([id]) => id));
}

// SQLite orders text by its bytes, which is code point order; JavaScript's `<` is UTF-16 order.
export function byCodePoint(a: string, b: string): number {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}

// ---- The statistics of the collection's dates.

// Each book's dating on both axes: one primary-key range per book of the answer.
export const bookDatesQuery = () => `SELECT b.id,b.key,d.axis,d.kind,d.start,d.end FROM json_each(?) j JOIN books b ON b.id=j.value
  LEFT JOIN book_dates d ON d.book=b.id`;
// The collection's dates in numbers: how many crops and works are dated, by hundred years and by decade
// of the year a work's copy is dated from, and by what the date dates (`tallyDates`). A work is a book;
// its collected crops and corpus glyphs are counted in the publication's answer, so no request reads a
// crop. The edge keeps one copy per publication and book-facts load.
export async function dateStats(env: Env, ctx: ExecutionContext, url: URL): Promise<Json> {
  const [stamps, { pub }] = await Promise.all([catalogueStamps(env), currentPointer(env)]);
  const key = new Request(`${url.origin}/atlas/dates/stats?v=${encodeURIComponent(`${pub}:${publicationKey(stamps)}`)}`);
  const cached = await caches.default.match(key);
  if (cached) return cached.json();
  const { books } = await bookCounts(env);
  const rows = (await env.ATLAS.prepare(bookDatesQuery()).bind(JSON.stringify([...books.keys()]))
    .all<{ id: number; key: string; axis: string | null; kind: string | null; start: number | null; end: number | null }>()).results;
  const facts = new Map<number, { document: string; kind: string | null; start: number | null; end: number | null; composed: number | null }>();
  for (const row of rows) {
    const fact = facts.get(row.id) ?? { document: row.key, kind: null, start: null, end: null, composed: null };
    if (row.axis === 'witness') Object.assign(fact, { kind: row.kind, start: row.start, end: row.end });
    if (row.axis === 'composed') fact.composed = row.start ?? row.end;
    facts.set(row.id, fact);
  }
  // The collection's books, then the corpus's, each in key order: one row a book's crops in one list.
  const list = (which: 0 | 1) => [...books].filter(([book, n]) => n[which] > 0 && facts.has(book))
    .map(([book, n]) => ({ ...facts.get(book)!, n: n[which] })).sort((a, b) => byCodePoint(a.document, b.document));
  const body = tallyDates([...list(0), ...list(1)]);
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': 'public, max-age=3600' } })));
  return body;
}

// ---- Corpus glyphs by character.

// Corpus glyphs by character in the material asked for, with the grapheme each is filed under: what
// the grapheme browser counts beside the collection's own crops, so a character the site holds only as
// corpus glyphs (債) is listed too. The answer changes only with a publication, and the edge keeps one
// copy per publication and material.
export async function corpusCharacters(env: Env, ctx: ExecutionContext, url: URL): Promise<Json> {
  const production = url.searchParams.get('production') || 'all';
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const { pub } = await currentPointer(env);
  const key = new Request(`${url.origin}/atlas/corpus/characters?production=${encodeURIComponent(production)}&v=${encodeURIComponent(pub)}`);
  const cached = await caches.default.match(key);
  if (cached) return cached.json();
  const { cells, chars } = await corpusCounts(env, false);
  const keys = await productionKeys(env, cells.map(c => c.production));
  const totals = new Map<number, number>();
  for (const c of cells) if (inScope(production, keys.get(c.production) ?? 'unknown')) totals.set(c.char, (totals.get(c.char) ?? 0) + c.n);
  // [label, grapheme, glyphs], one per character that has any.
  const items = [...totals].filter(([char, n]) => n > 0 && chars.has(char)).map(([char, n]): [string, string, number] => [chars.get(char)!.text, chars.get(char)!.grapheme, n])
    .sort((a, b) => byCodePoint(a[0], b[0]));
  const body = { items };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': 'public, max-age=3600' } })));
  return body;
}

// ---- The homepage gallery.

// The homepage gallery is dealt from the publication's sample (`gallery/` in answers.py): the corpus
// glyphs whose shuffle falls below one sixty-fourth of its range, records copied in, so a page reads one
// or two objects. A script group deals the sample's glyphs of its category; the scripts the corpus
// holds few glyphs of are copied whole (WHOLE_GROUPS), so their seed's point falls anywhere in the
// shuffle range. A glyph a review has relabelled shows its row's values, and leaves a group it no
// longer belongs to.
const GALLERY_GROUPS = ['kana', 'kanji', 'hangul', 'gugyeol', 'tangut', 'other'], WHOLE_GROUPS = ['hangul', 'gugyeol', 'tangut'];
export async function gallery(env: Env, q: URLSearchParams): Promise<Json> {
  const group = q.get('group') && q.get('group') !== 'all' ? q.get('group')! : null;
  if (group && !GALLERY_GROUPS.includes(group)) throw new Problem(400, 'Invalid group.');
  const limit = integer(q, 'limit', 24, 96), seed = integer(q, 'seed', 0, 2147483647);
  const { pub } = await currentPointer(env);
  const name = group && WHOLE_GROUPS.includes(group) ? group : 'sample', pool = (await galleryIndex(env, pub))[name];
  const start = seed % pool.range, first = Math.floor(start * pool.shards / pool.range);
  const wanted = (entry: SampleEntry) => name !== 'sample' || !group || entry[2] === group;
  // From the seed's point up through the shards and round to it again: the glyphs of the seed's own
  // shard below its point come last. A pool or group with no glyph reads no shard.
  const chosen: SampleEntry[] = [];
  const any = pool.glyphs > 0 && (name !== 'sample' || !group || (pool.categories[group] ?? 0) > 0);
  for (let i = 0; any && i <= pool.shards && chosen.length < limit; i++) {
    const entries = await galleryShard(env, pub, name, (first + i) % pool.shards);
    for (const entry of entries) if (wanted(entry) && (i === 0 ? entry[1] >= start : i < pool.shards || entry[1] < start)) chosen.push(entry);
  }
  const page = chosen.slice(0, limit);
  // A mark is the review queue's: a glyph a round showed or found hard is pending, as its record says.
  const items = (await listingItems(env, page.map(e => e[0]), new Map<number, Entry>(page.map(([crop, , , record]) => [crop, record]))))
    .filter(item => !group || categoryOf(String(item.label ?? '')) === group)
    .map(item => item.state === 'seen' || item.state === 'hard' ? { ...item, state: 'pending' } : item);
  return { status: 'ok', available: items.length, items };
}

// ---- The catalogue.

// A Quick review round leaves movable type out unless asked: it fills whole books with near-identical glyphs.
const REVIEW_SCOPE = 'not:printed/type';
// How far a round pages. Rounds are dealt from the front and a saved crop leaves the queue, so a
// reader never gets this deep; the bound keeps a crawler from reading a character's whole corpus.
const ROUND_OFFSET_MAX = 4096;
// How long a crop a reviewer skipped stays out of that reviewer's own rounds.
const SKIP_REST_MS = 3 * 24 * 60 * 60 * 1000;
// Shuffle is a 31-bit hash.
const SHUFFLE_RANGE = 2147483648;
// Browse counts every collected crop by character, book and state, and every visitor gets the same
// answer: the edge keeps one copy per catalogue version.
const FACETS_TTL = 3600;
const STATE_NAMES = ['pending', 'seen', 'flagged', 'checked', 'hard', 'skipped'] as const;
type StateName = typeof STATE_NAMES[number];

// What a listing keeps: characters (null for any), a book, a category (null for any, -1 for none), a
// state code, withheld crops or not, the production ids in scope (null for any), and named crops only.
export type Filter = { chars: Set<number> | null; book: number | null; category: number | null; state: number | null;
  withheld: boolean; productions: Set<number> | null; named: boolean };
type Row = { char: number | null; book: number; production: number | null; category: number | null; state: number; quiz: number };
const matches = (f: Filter, r: Row) => (f.chars === null || (r.char !== null && f.chars.has(r.char))) && (!f.named || (r.char ?? -1) !== -1)
  && (f.book === null || r.book === f.book) && (f.category === null || (r.category ?? -1) === f.category)
  && (f.state === null || r.state === f.state) && (f.withheld || r.quiz === 1)
  && (f.productions === null || f.productions.has(r.production ?? -1));

// A filter's conditions on the crops columns, beside the index range a listing reads.
function conditions(f: Filter, skip: { chars?: boolean; book?: boolean; category?: boolean } = {}, column = (name: string) => name) {
  const where: string[] = [], values: (number | string)[] = [];
  if (!skip.book && f.book !== null) { where.push(`${column('book')}=?`); values.push(f.book); }
  if (!skip.category && f.category !== null) {
    if (f.category === -1) where.push(`${column('category')} IS NULL`);
    else { where.push(`${column('category')}=?`); values.push(f.category); }
  }
  if (f.state !== null) { where.push(`${column('state')}=?`); values.push(f.state); }
  if (!f.withheld) where.push(`${column('quiz')}=1`);
  if (!skip.chars && f.chars !== null) { where.push(`${column('char')} IN (SELECT value FROM json_each(?))`); values.push(JSON.stringify([...f.chars])); }
  else if (!skip.chars && f.named) where.push(`${column('char')} IS NOT NULL`);
  if (f.productions !== null) { where.push(`coalesce(${column('production')},-1) IN (SELECT value FROM json_each(?))`); values.push(JSON.stringify([...f.productions])); }
  return { sql: where.map(w => ` AND ${w}`).join(''), values };
}

// One index range of a listing, read from the seed's point up (`>=`) or below it (`<`), in shuffle order:
// one per character named, else the book's, else one per script category.
export type Range = { from: string; values: (number | string)[] };
export function ranges(f: Filter, kind: 0 | 1, categories: number[]): Range[] {
  if (f.chars !== null) {
    const rest = conditions(f, { chars: true });
    return [...f.chars].sort((a, b) => a - b).map(char => ({ from: `crops INDEXED BY crop_deal WHERE char=? AND kind=? AND kind<2${rest.sql}`,
      values: [char, kind, ...rest.values] }));
  }
  if (f.book !== null) {
    const rest = conditions(f, { book: true });
    return [{ from: `crops INDEXED BY crop_book WHERE book=? AND kind<2 AND kind=0${rest.sql}`, values: [f.book, ...rest.values] }];
  }
  const rest = conditions(f, { category: true });
  return (f.category !== null ? [f.category] : [...categories, -1]).map(code => ({
    from: `crops INDEXED BY crop_listing WHERE ${code === -1 ? 'category IS NULL' : 'category=?'} AND kind=0${rest.sql}`,
    values: [...(code === -1 ? [] : [code]), ...rest.values] }));
}
export const sideQuery = (range: Range, side: '>=' | '<', offset = false) =>
  `SELECT id,shuffle FROM ${range.from} AND shuffle${side}? ORDER BY shuffle,id LIMIT ?${offset ? ' OFFSET ?' : ''}`;
type Dealt = { id: number; shuffle: number };

/** Positions `offset` to `offset + limit` of the crops the ranges hold, from the seed's point up and
 *  round past the highest, leaving out `exclude`. `above` is how many lie from the point up, once a page
 *  has reached them all: a later page that names it (`told`) reads only the crops below. One range with
 *  nothing left out is paged in SQL; several are merged, each read to the page's end. */
export async function deal(env: Env, list: Range[], start: number, offset: number, limit: number, exclude: Set<number>,
  told: number | null = null): Promise<{ ids: number[]; above: number | null }> {
  if (!list.length || limit <= 0) return { ids: [], above: told };
  const read = async (side: '>=' | '<', n: number, skip = 0) => (await env.ATLAS.batch<Dealt>(list.map(range =>
    env.ATLAS.prepare(sideQuery(range, side, skip > 0)).bind(...range.values, start, n, ...(skip > 0 ? [skip] : [])))))
    .flatMap(r => r.results).sort((a, b) => a.shuffle - b.shuffle || a.id - b.id).filter(row => !exclude.has(row.id));
  const one = list.length === 1 && !exclude.size, room = exclude.size;
  if (told !== null && offset >= told) {
    const below = one ? await read('<', limit, offset - told) : (await read('<', offset - told + limit + room)).slice(offset - told);
    return { ids: below.slice(0, limit).map(r => r.id), above: told };
  }
  const up = one ? await read('>=', limit, offset) : await read('>=', offset + limit + room);
  const page = one ? up : up.slice(offset);
  if (page.length >= limit) return { ids: page.slice(0, limit).map(r => r.id), above: null };
  // The point's side ran out: every crop from it up is read, or counted, and the page wraps round.
  let above: number;
  if (!one) above = up.length;
  else if (up.length) above = offset + up.length;
  else above = told ?? (await env.ATLAS.prepare(`SELECT count(*) AS n FROM ${list[0].from} AND shuffle>=?`).bind(...list[0].values, start).first<{ n: number }>())!.n;
  const rest = limit - page.length, skip = Math.max(offset - above, 0);
  const below = one ? await read('<', rest, skip) : (await read('<', skip + rest + room)).slice(skip);
  return { ids: [...page, ...below].slice(0, limit).map(r => r.id), above };
}

// The members of a grapheme a round names, by the grapheme table: は's family is は, ハ and its
// hentaigana. A key that names no family is its own text alone; a key that names a family's member
// rather than the family is refused. The head comes first, then the members by id.
export const graphemeQuery = () => 'SELECT id,text,head FROM graphemes WHERE text=?';
export const membersQuery = () => 'SELECT id,text FROM graphemes WHERE head=? ORDER BY id';
export async function graphemeMembers(env: Env, key: string): Promise<{ ids: number[]; texts: string[] }> {
  const own = compose(graphemeText(key));
  const row = await env.ATLAS.prepare(graphemeQuery()).bind(own).first<{ id: number; text: string; head: number | null }>();
  if (!row) return { ids: [], texts: [own] };
  if (row.head !== null && row.head !== row.id) {
    const head = await env.ATLAS.prepare('SELECT text FROM graphemes WHERE id=?').bind(row.head).first<{ text: string }>();
    throw new Problem(422, `${graphemeText(key)} is filed under ${head?.text}.`);
  }
  const members = (await env.ATLAS.prepare(membersQuery()).bind(row.id).all<{ id: number; text: string }>()).results;
  const all = [{ id: row.id, text: row.text }, ...members.filter(m => m.id !== row.id)];
  return { ids: all.map(m => m.id), texts: all.map(m => m.text) };
}

// A reviewer's own skips that still rest: from a submission not undone, at the crop's current cut, of a
// pending crop that may be dealt. One `skip_actor` range per actor id of the reviewer's account, each
// crop by its key (CROSS JOIN keeps `skips` outside).
export const ownSkipsQuery = () => `SELECT c.id,c.kind,c.char,c.book,c.production,c.category,c.state,c.quiz FROM skips k
  CROSS JOIN crops c ON c.id=k.crop WHERE k.actor IN (SELECT id FROM actors WHERE user=?) AND k.at>? AND k.cut=c.cut
  AND c.kind<2 AND c.state=0 AND c.quiz=1 AND c.char IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.id=k.submission AND s.undone=1)`;
// Crops another reviewer skipped, at the crop's current cut, from a submission not undone: a round
// deals them first, since they need a second pair of eyes. `skips` keeps only the days a publication
// leaves in D1 (decision record 0005), so it is read whole, and each crop by its key.
export const othersSkipsQuery = () => `SELECT DISTINCT c.id,c.kind,c.char,c.book,c.production,c.category,c.state,c.quiz,c.shuffle FROM skips k
  CROSS JOIN crops c ON c.id=k.crop WHERE k.cut=c.cut AND c.kind<2 AND c.char IS NOT NULL
  AND k.actor NOT IN (SELECT id FROM actors WHERE user=?)
  AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.id=k.submission AND s.undone=1)`;

// A standing review from the character inspector, not a round's own verdict: a review event (match,
// wrong or unsure) of a submission not undone whose request names its crop (`target`); a round's names none.
export const REVIEWED_IN_INSPECTOR = `EXISTS(SELECT 1 FROM events e JOIN submissions s ON s.id=e.submission AND s.undone=0
  WHERE e.crop=c.id AND e.kind IN (0,1,2) AND json_extract(s.request,'$.target') IS NOT NULL)`;
// The Flagged view: every collected crop flagged or hard to read, from `crop_flagged`, which holds only them.
export function flaggedQuery(f: Filter): { sql: string; values: (number | string)[] } {
  const rest = conditions(f, {}, name => `c.${name}`);
  return { sql: `SELECT c.id,c.shuffle,c.char,c.book,c.production,c.category,c.state,c.quiz,${REVIEWED_IN_INSPECTOR} AS reported
    FROM crops c INDEXED BY crop_flagged WHERE c.kind=0 AND c.state IN (2,4)${rest.sql}`, values: rest.values };
}
// The order the Flagged view shows its few crops in, from the seed: a hash of the shuffle, then id.
const seeded = (shuffle: number, seed: number) => Number((BigInt(shuffle) * BigInt(seed + 1)) % 2147483647n);

type Category = { label: string; grapheme: string; total: number } & Record<StateName, number>;
type Book = { id: string; title: string | null; total: number } & Record<StateName, number>;
const empty = () => ({ total: 0, pending: 0, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 });

export const booksQuery = () => 'SELECT id,key,title FROM books WHERE id IN (SELECT value FROM json_each(?))';
export const bookQuery = () => 'SELECT id FROM books WHERE key=?';

export async function catalogue(env: Env, ctx: ExecutionContext, url: URL, reviewer: string | null): Promise<Json> {
  const q = url.searchParams;
  const purpose = q.get('purpose') || 'browse', review = purpose === 'review';
  const production = q.get('production') || (review ? REVIEW_SCOPE : 'all');
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const seed = integer(q, 'seed', 0, 2147483647);
  const limit = integer(q, 'limit', 60, 96), offset = integer(q, 'offset', 0);
  if (review && offset > ROUND_OFFSET_MAX) throw new Problem(404, 'A round does not page this far.');
  const since = Date.now() - SKIP_REST_MS;
  // An empty `character` names none. A round names its grapheme, and deals every character of it.
  const character = q.get('character') || null;
  const grapheme = graphemeKey(q.get('grapheme'));
  const scoped = review && grapheme !== null;
  const members = scoped ? await graphemeMembers(env, grapheme) : null;
  const stateParam = q.get('state') || 'all';
  // The Flagged view shows every crop waiting for a person, withheld or not.
  const flaggedView = ['flagged', 'attention'].includes(stateParam);
  // A round deals only crops it may show, whatever `withheld` asks.
  const withheld = !review && (flaggedView || q.get('withheld') === 'show');
  const groupParam = q.get('group') && q.get('group') !== 'all' ? q.get('group')! : null;
  const documentKey = text(q.get('document'), 256, 'document');
  const searched = q.get('q') ? literal(q.get('q')!) : null;

  const [{ cells, chars }, held, stamps] = await Promise.all([collectedCounts(env), codeNames(env), catalogueStamps(env)]);
  const keys = await productionKeys(env, cells.map(c => c.production));
  // A publication may add a code the isolate does not hold yet: the table is read again, once.
  const names = cells.some(c => !held.has(`state:${c.state}`) || (c.category !== null && c.category !== -1 && !held.has(`category:${c.category}`)))
    ? await codeNames(env, true) : held;
  const code = (field: string, key: string) => {
    for (const [k, v] of names) if (v === key && k.startsWith(field + ':')) return Number(k.slice(field.length + 1));
    return null;
  };
  const stateOf = new Map([...names].filter(([k]) => k.startsWith('state:')).map(([k, v]) => [Number(k.slice(6)), v as StateName]));
  const categoryCodes = [...names].filter(([k]) => k.startsWith('category:')).map(([k]) => Number(k.slice(9))).sort((a, b) => a - b);
  const productions = scopeIds(production, keys);

  // The characters a listing names: a round's grapheme members, a browse page's family (every character
  // filed under the grapheme), a character, a search. A name the site holds no crop of names none.
  const byText = new Map([...chars].map(([id, info]) => [info.text, id]));
  let named: Set<number> | null = null;
  const narrow = (ids: Iterable<number>) => { const next = new Set(ids); named = named === null ? next : new Set([...named].filter(id => next.has(id))); };
  if (members) narrow(members.ids);
  if (grapheme && !scoped) narrow([...chars].filter(([, info]) => info.grapheme === grapheme).map(([id]) => id));
  for (const value of [character, searched]) if (value) {
    const id = byText.get(value) ?? (await env.ATLAS.prepare(graphemeQuery()).bind(value).first<{ id: number }>())?.id;
    narrow(id === undefined ? [] : [id]);
  }
  // -2 matches nothing: a book, script or state the site does not know.
  const book = documentKey ? (await env.ATLAS.prepare(bookQuery()).bind(documentKey).first<{ id: number }>())?.id ?? -2 : null;
  const category = groupParam ? code('category', groupParam) ?? -2 : null;
  const wantedState = stateParam === 'attention' || stateParam === 'all' || stateParam === 'skipped' ? null : code('state', stateParam) ?? -2;

  // A reviewer's own recent skips are theirs: those crops count as `skipped`, and a round leaves them out.
  const own = review && reviewer ? (await env.ATLAS.prepare(ownSkipsQuery()).bind(reviewer, since).all<Row & { id: number; kind: number }>()).results : [];
  const glyphs = review ? await corpusCounts(env, true) : null;
  // Counts by character and state, and for browsing by book as well. A round counts only the crops it
  // may deal (named, not withheld) and, naming a grapheme, only its members'.
  const counted: Filter = { chars: members ? new Set(members.ids) : null, book: null, category: null, state: null,
    withheld, productions, named: review };
  const facetsKey = review ? null : new Request(`${url.origin}/atlas/facets?production=${encodeURIComponent(production)}&withheld=${
    withheld ? 'show' : 'hide'}&v=${encodeURIComponent(catalogueKey(stamps))}`);
  const hit = facetsKey ? await caches.default.match(facetsKey) : undefined;
  const facets: { counts: Record<StateName, number>; categories: Category[]; documents: Book[] } = hit ? await hit.json()
    : await countFacets(env, { cells, chars, glyphs, own, counted, stateOf, review });
  if (facetsKey && !hit) ctx.waitUntil(caches.default.put(facetsKey, Response.json(facets, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));

  // The listing.
  const filter: Filter = { chars: named, book, category, state: wantedState, withheld, productions, named: review };
  const listedCount = (f: Filter) => cells.reduce((n, c) => n + (matches(f, c) ? c.n : 0), 0);
  const exclude = new Set<number>(own.map(row => row.id));
  const wantsRows = limit > 0;
  let ids: number[] = [], total: number, next = offset, above: number | null = null, reportedCount = 0;
  if (flaggedView) {
    // `attention` is every crop waiting for a person, flagged or hard to read; `flagged` the flagged ones.
    // The Flagged view hides crops already looked at in the inspector on `reported=hide`, and counts them.
    const { sql, values } = flaggedQuery(filter);
    const rows = (await env.ATLAS.prepare(sql).bind(...values).all<Row & { id: number; shuffle: number; reported: number }>()).results;
    reportedCount = rows.filter(row => row.reported).length;
    const shown = q.get('reported') === 'hide' ? rows.filter(row => !row.reported) : rows;
    // A crop already looked at in the inspector queues behind the ones nobody has reviewed yet.
    shown.sort((a, b) => a.reported - b.reported || seeded(a.shuffle, seed) - seeded(b.shuffle, seed) || a.id - b.id);
    total = shown.length;
    ids = wantsRows ? shown.slice(offset, offset + limit).map(row => row.id) : [];
    next = offset + ids.length;
  } else if (stateParam === 'skipped') {
    const mine = own.filter(row => row.kind === 0 && matches(filter, row)).map(row => row.id).sort((a, b) => a - b);
    total = mine.length;
    ids = wantsRows ? mine.slice(offset, offset + limit) : [];
    next = offset + ids.length;
  } else {
    const start = seed % SHUFFLE_RANGE;
    // A grapheme's round of pending crops deals its corpus glyphs after its collected crops. Corpus
    // glyphs belong to no book of the collection, so a round narrowed to one deals none.
    const corpus = scoped && !character && book === null && (wantedState === null || wantedState === 0) && !searched
      && (category === null || members!.texts.every(member => categoryOf(member) === groupParam));
    const corpusFilter: Filter = { ...filter, state: 0, category: null };
    // A round deals what another reviewer skipped first, then the collected crops, then the corpus
    // glyphs, each crop once.
    const head = review ? (await env.ATLAS.prepare(othersSkipsQuery()).bind(reviewer).all<Row & { id: number; kind: number; shuffle: number }>()).results
      .filter(row => !exclude.has(row.id) && (row.kind === 0 ? matches(filter, row) : corpus && matches(corpusFilter, row)))
      .sort((a, b) => (a.shuffle - start + SHUFFLE_RANGE) % SHUFFLE_RANGE - (b.shuffle - start + SHUFFLE_RANGE) % SHUFFLE_RANGE || a.id - b.id) : [];
    for (const row of head) exclude.add(row.id);
    const left = (kind: number, f: Filter) => own.filter(row => row.kind === kind && matches(f, row)).length + head.filter(row => row.kind === kind).length;
    const local = listedCount(filter) - left(0, filter);
    let glyphTotal = 0;
    if (corpus) for (const c of glyphs!.cells) if (matches(corpusFilter, { ...c, book: -1, category: null })) glyphTotal += c.n;
    if (corpus) glyphTotal -= left(1, corpusFilter);
    total = head.length + local + glyphTotal;
    if (wantsRows) {
      // Positions run through the head, the collected crops and the corpus glyphs. Should a list hold
      // fewer than its count says, the next one starts where its positions begin.
      let at = offset;
      if (at < head.length) { ids.push(...head.slice(at, at + limit).map(row => row.id)); at = Math.min(head.length, at + limit); }
      const told = review || q.get('above') === null ? null : integer(q, 'above', 0);
      if (ids.length < limit && at < head.length + local) {
        const want = limit - ids.length, page = await deal(env, ranges(filter, 0, categoryCodes), start, at - head.length, want, exclude, told);
        ids.push(...page.ids);
        if (!review) above = page.above;
        at = page.ids.length < want ? head.length + local : at + page.ids.length;
      }
      if (corpus && ids.length < limit && at >= head.length + local) {
        const want = limit - ids.length, page = await deal(env, ranges(corpusFilter, 1, categoryCodes), start, at - head.length - local, want, exclude);
        ids.push(...page.ids);
        at += page.ids.length;
      }
      next = at;
    }
  }
  let items = wantsRows ? await listed(env, ids, new Set(own.map(row => row.id))) : [];
  // A corpus glyph whose image this site may not serve is not dealt; it still takes its position.
  items = items.filter(item => item.origin !== 'corpus' || item.proxyable);
  // How many withheld crops browsing left out, of a character, a book or a family where one is named;
  // with a script or a state chosen the count is not known.
  const withheldCount = !review && !withheld && !groupParam && stateParam === 'all'
    ? listedCount({ chars: named, book, category: null, state: null, withheld: true, productions, named: false })
      - listedCount({ chars: named, book, category: null, state: null, withheld: false, productions, named: false })
    : null;
  return { total, next_offset: next, available: Object.values(facets.counts).reduce((a, b) => a + b, 0),
    counts: facets.counts, purpose, production, review_limit: ROUND_MAX, review_epoch: await siteValue(env, 'review_epoch') || 0,
    query: q.get('q'), ...(scoped ? { grapheme: { code_point: grapheme, char: graphemeText(grapheme), members: members!.texts } } : {}),
    categories: facets.categories, documents: facets.documents, reported_count: reportedCount, withheld_count: withheldCount, above, items };
}

// Counts by character and state, and for browsing by book as well, with each book's key and title. A
// reviewer's own recent skips move from `pending` to `skipped`; a round adds each character's corpus
// glyphs nothing has decided, as pending, under the grapheme its family files it under.
async function countFacets(env: Env, { cells, chars, glyphs, own, counted, stateOf, review }: {
  cells: Awaited<ReturnType<typeof collectedCounts>>['cells']; chars: Awaited<ReturnType<typeof collectedCounts>>['chars'];
  glyphs: Awaited<ReturnType<typeof corpusCounts>> | null; own: (Row & { id: number; kind: number })[]; counted: Filter;
  stateOf: Map<number, StateName>; review: boolean }) {
  const counts = Object.fromEntries(STATE_NAMES.map(s => [s, 0])) as Record<StateName, number>;
  const categories = new Map<string, Category>(), documents = new Map<number, Book>();
  const info = (char: number) => chars.get(char) ?? glyphs?.chars.get(char) ?? null;
  const add = (char: number, state: StateName, n: number, book: number | null) => {
    counts[state] += n;
    const found = char === -1 ? null : info(char);
    if (found) {
      const entry = categories.get(found.text) ?? { label: found.text, grapheme: found.grapheme, ...empty() };
      entry.total += n; entry[state] += n; categories.set(found.text, entry);
    }
    if (book === null) return;
    const entry = documents.get(book) ?? { id: '', title: null, ...empty() };
    entry.total += n; entry[state] += n; documents.set(book, entry);
  };
  const stored = new Map<string, { char: number; book: number | null; state: StateName; n: number }>();
  for (const c of cells) {
    if (!matches(counted, c)) continue;
    const state = stateOf.get(c.state) ?? 'pending', book = review ? null : c.book, key = `${c.char}:${book}:${state}`;
    const found = stored.get(key);
    if (found) found.n += c.n; else stored.set(key, { char: c.char, book, state, n: c.n });
  }
  const skipped = new Map<string, number>();
  for (const row of own) if (matches(counted, row)) skipped.set(`${row.kind}:${row.char}`, (skipped.get(`${row.kind}:${row.char}`) ?? 0) + 1);
  for (const [key, n] of skipped) {
    const [kind, char] = key.split(':').map(Number);
    if (kind !== 0) continue;
    const pending = stored.get(`${char}:null:pending`), moved = Math.min(n, pending?.n ?? 0);
    if (!moved) continue;
    pending!.n -= moved;
    const target = stored.get(`${char}:null:skipped`) ?? { char, book: null, state: 'skipped' as StateName, n: 0 };
    target.n += moved; stored.set(`${char}:null:skipped`, target);
  }
  for (const row of stored.values()) if (row.n > 0) add(row.char, row.state, row.n, row.book);
  if (glyphs) {
    const pending = new Map<number, number>();
    for (const c of glyphs.cells) if (c.state === 0 && matches({ ...counted, state: null }, { ...c, book: -1, category: null }))
      pending.set(c.char, (pending.get(c.char) ?? 0) + c.n);
    for (const [char, n] of pending) {
      const moved = Math.min(skipped.get(`1:${char}`) ?? 0, n);
      if (n - moved > 0) add(char, 'pending', n - moved, null);
      if (moved) add(char, 'skipped', moved, null);
    }
  }
  if (documents.size) for (const row of (await env.ATLAS.prepare(booksQuery()).bind(JSON.stringify([...documents.keys()]))
    .all<{ id: number; key: string; title: string | null }>()).results) Object.assign(documents.get(row.id)!, { id: row.key, title: row.title });
  return { counts,
    categories: [...categories.values()].sort((a, b) => b.total - a.total || a.label.localeCompare(b.label)),
    documents: [...documents.values()].sort((a, b) => b.total - a.total || (a.title ?? '').localeCompare(b.title ?? '') || a.id.localeCompare(b.id)) };
}

// A page's items with each crop's suspect mark and shape order, and its state as the reviewer sees it:
// a crop of their own recent skips is `skipped` for them.
async function listed(env: Env, ids: number[], skipped: Set<number>): Promise<Json[]> {
  if (!ids.length) return [];
  const [items, marks] = await Promise.all([listingItems(env, ids), marksOf(env, ids)]);
  const crops = new Map(ids.map(id => [publicId(id), id]));
  return items.map(item => {
    const id = crops.get(item.id)!, mark = marks.get(id);
    return { ...item, state: skipped.has(id) && item.state === 'pending' ? 'skipped' : item.state, shape_order: mark?.shape_order ?? null,
      suspect: suspectOf(mark?.suspect, item) };
  });
}
