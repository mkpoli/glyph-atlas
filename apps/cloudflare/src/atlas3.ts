import { readRecords, recordKey } from './records';
import type { Timing } from './timing';

// Reads of the new storage layout (docs/decisions/0005-storage-rebuild.md): the crop index in the D1
// database bound as ATLAS, and page bundles in R2 under `v3/pages/`.

type Json = any;

// A crop's id is its page's id times 2^24 plus its slot on the page, below 2^53. JavaScript's bitwise
// operators are 32-bit, so the parts are taken by multiplication and division.
export const PAGE = 2 ** 24;
export const cropId = (page: number, slot: number) => page * PAGE + slot;
export const pageOf = (id: number) => Math.floor(id / PAGE);
export const slotOf = (id: number) => id % PAGE;

// Public URLs write the id in Crockford base 32, lower case. Reading takes either case and the letters
// Crockford reads as digits (i and l as 1, o as 0). A leading zero is refused, so a crop has one URL.
const DIGITS = '0123456789abcdefghjkmnpqrstvwxyz';
export function publicId(id: number): string {
  let out = '';
  do { out = DIGITS[id % 32] + out; id = Math.floor(id / 32); } while (id > 0);
  return out;
}
export function cropOf(text: string): number | null {
  if (!/^[0-9a-z]{1,11}$/i.test(text) || /^[0o]/i.test(text)) return null;
  let id = 0;
  for (const c of text.toLowerCase().replace(/[il]/g, '1').replace(/o/g, '0')) {
    const digit = DIGITS.indexOf(c);
    if (digit < 0) return null;
    id = id * 32 + digit;
  }
  return Number.isSafeInteger(id) && pageOf(id) > 0 ? id : null;
}

// A page bundle: the page's facts and one entry per crop, by slot. Bundles are immutable under their
// revision, so an isolate keeps the last few it read, and the edge keeps each one it served for a day
// under a URL of this site that no route answers: an isolate new to a page reads it from R2 once per
// location.
export type Entry = Json & { slot: number; id: string };
export type Bundle = { key: string; book: number; rev: number; page: Json | null; bySlot: Map<number, Entry> };
export const bundleKey = (page: number, rev: number) => `v3/pages/${page}-${rev}.json.gz`;
/** Whether this isolate holds a page's bundle at a revision. */
export const holdsBundle = (page: number, rev: number) => held.has(bundleKey(page, rev));
// A crop's bundle entry by itself, at the edge under a URL no route answers. An entry never changes under
// its page's revision, so a listing that meets a crop again, whatever has been reviewed since, reads
// its entry instead of the page's whole bundle, which can hold hundreds of crops.
const entryEdge = (page: number, rev: number, slot: number) => new Request(`https://glyphatlas.org/_entries/${page}-${rev}/${slot}`);
const BUNDLES_HELD = 64, BUNDLE_EDGE_SECONDS = 86400;
const held = new Map<string, Promise<Bundle | null>>();
/** Where the bundles a call read came from: the isolate, the edge, or R2. */
export type BundleSources = { held: number; edge: number; r2: number };
export function bundle(env: Env, page: number, rev: number, sources?: BundleSources): Promise<Bundle | null> {
  const key = bundleKey(page, rev), hit = held.get(key);
  if (hit) { held.delete(key); held.set(key, hit); if (sources) sources.held++; return hit; }
  const read = (async () => {
    const edge = new Request(`https://glyphatlas.org/_bundles/${key}`);
    let bytes = await caches.default.match(edge).then(hit => hit?.arrayBuffer() ?? null);
    if (sources) sources[bytes ? 'edge' : 'r2']++;
    if (!bytes) {
      const object = await env.MEDIA.get(key);
      if (!object) return null;
      bytes = await new Response(object.body).arrayBuffer();
      await caches.default.put(edge, new Response(bytes, { headers: { 'cache-control': `public, max-age=${BUNDLE_EDGE_SECONDS}, immutable` } }))
        .catch(() => {});
    }
    // Bundles are stored gzipped and served with that encoding; the binding hands back the stored bytes.
    const body = await new Response(new Response(bytes).body!.pipeThrough(new DecompressionStream('gzip'))).json() as Json;
    return { key: body.key, book: body.book, rev: body.rev, page: body.page ?? null,
      bySlot: new Map((body.crops as Entry[]).map(entry => [entry.slot, entry])) };
  })();
  // A failed read is not kept, so the next request reads again; a newer read under the key stays.
  read.catch(() => { if (held.get(key) === read) held.delete(key); });
  held.set(key, read);
  while (held.size > BUNDLES_HELD) held.delete(held.keys().next().value!);
  return read;
}

// Small code tables (state, style, category, issue), the same for every request until a publication adds a
// code: a listing that meets a code the isolate does not hold reads the table again, once.
let codes: Promise<Map<string, string>> | null = null;
export function codeNames(env: Env, fresh = false): Promise<Map<string, string>> {
  if (fresh || !codes) {
    const read = env.ATLAS.prepare(`SELECT field,code,key FROM codes WHERE field IN ('state','style','category','issue')`)
      .all<{ field: string; code: number; key: string }>()
      .then(r => new Map(r.results.map(row => [`${row.field}:${row.code}`, row.key])));
    read.catch(() => { if (codes === read) codes = null; });
    codes = read;
  }
  return codes;
}

export type CropRow = { id: number; kind: number; char: string | null; production: string | null; category: number | null;
  style: number | null; state: number; quiz: number; year: number | null; cut: number; revision: number; flags: number;
  rev: number; page_key: string; document: string };
// One read for a bounded list of crops: each crop's row with its page's revision, its character,
// book and production by name. Each join is a primary-key or unique-key lookup.
export const cropRowsQuery = () => `SELECT c.id,c.kind,g.text AS char,p.key AS production,c.category,c.style,c.state,c.quiz,
  c.year,c.cut,c.revision,c.flags,pg.rev,pg.key AS page_key,b.key AS document
  FROM json_each(?) j JOIN crops c ON c.id=j.value JOIN pages pg ON pg.id=c.id>>24 JOIN books b ON b.id=c.book
  LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN productions p ON p.id=c.production`;

// Fields only a crop's inspector reads, where the images sit in their packs, which only the image route
// reads, and the cuts a crop has had, which only its versions read.
const UNLISTED = ['text', 'line', 'context_image', 'context_box', 'crop_box', 'image_place', 'context_image_place', 'record_place', 'slot',
  'cuts', 'cut_at', 'encodings'];
// The item a listing shows for each crop, in the order asked: the bundle's record under the row's
// current values. A crop with no row, a candidate, or one its bundle does not hold is left out, and so
// is a corpus glyph whose record (at `record_place` in a pack) is missing or does not parse. A storage
// error fails the call, as `readRecords` explains. A caller that holds a crop's entry already, with
// its record in place of `record_place` (the homepage sample), passes it in `given`, and that crop's
// bundle is not read. One that read the crops' rows already (a run's page) passes them in `known`. With
// `timing`, the rows, the entries, the bundles and the records are timed, and the bundles' sources noted.
// A crop's entry is read from the edge (`entryEdge`) unless the isolate holds its page's bundle; with
// `ctx`, each entry read from a bundle is kept there.
export type CropReads = { given?: Map<number, Entry>; known?: CropRow[]; timing?: Timing; ctx?: ExecutionContext };
export async function cropItems(env: Env, ids: number[], { given = new Map(), known, timing, ctx }: CropReads = {}): Promise<Json[]> {
  if (!ids.length) return [];
  const timed = <T>(name: string, work: Promise<T>) => timing ? timing.beside(name, work) : work;
  const rows = known ?? (await timed('rows', env.ATLAS.prepare(cropRowsQuery()).bind(JSON.stringify(ids)).all<CropRow>())).results;
  const byId = new Map(rows.map(row => [row.id, row]));
  const unread = rows.filter(row => !given.has(row.id));
  const asked = unread.filter(row => !holdsBundle(pageOf(row.id), row.rev));
  const kept = new Map(await timed('entries', Promise.all(asked.map(async row =>
    [row.id, await caches.default.match(entryEdge(pageOf(row.id), row.rev, slotOf(row.id))).then(hit => hit ? hit.json() as Promise<Entry> : null)] as const))));
  const pages = new Map(unread.filter(row => !kept.get(row.id)).map(row => [pageOf(row.id), row.rev]));
  const sources: BundleSources = { held: 0, edge: 0, r2: 0 };
  const bundles = new Map(await timed('bundles', Promise.all([...pages].map(async ([page, rev]) => [page, await bundle(env, page, rev, sources)] as const))));
  timing?.note('bundles-from', `kept ${[...kept.values()].filter(Boolean).length} held ${sources.held} edge ${sources.edge} r2 ${sources.r2}`);
  const fromBundle = (row: CropRow) => bundles.get(pageOf(row.id))?.bySlot.get(slotOf(row.id));
  if (ctx) for (const row of asked) {
    const entry = kept.get(row.id) ? undefined : fromBundle(row);
    if (entry) ctx.waitUntil(caches.default.put(entryEdge(pageOf(row.id), row.rev, slotOf(row.id)),
      Response.json(entry, { headers: { 'cache-control': `public, max-age=${BUNDLE_EDGE_SECONDS}, immutable` } })).catch(() => {}));
  }
  const entries = new Map(ids.map(id => { const row = byId.get(id);
    return [id, !row ? undefined : given.get(id) ?? kept.get(id) ?? fromBundle(row)] as const; }));
  const pointer = (entry: Entry) => ({ object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] });
  const wanted = [...entries.values()].filter(entry => entry?.record_place).map(entry => pointer(entry!));
  const records = await timed('records', readRecords(env, wanted));
  const keys = rows.flatMap(row => [`state:${row.state}`, `style:${row.style}`, `category:${row.category}`]).filter(key => !key.endsWith(':null'));
  let names = await codeNames(env);
  if (keys.some(key => !names.has(key))) names = await codeNames(env, true);
  const name = (field: string, code: number | null) => code === null ? null : names.get(`${field}:${code}`) ?? null;
  const items: Json[] = [];
  for (const id of ids) {
    const row = byId.get(id), entry = entries.get(id);
    if (!row || !entry || row.kind === 2 || entry.retired) continue;
    const whole = entry.record_place ? records.get(recordKey(pointer(entry))) : entry;
    if (!whole) continue;
    const record = Object.fromEntries(Object.entries(whole).filter(([k]) => !UNLISTED.includes(k)));
    // A corpus glyph with no character yet keeps the label its source gave it.
    items.push({ ...record, id: publicId(id), source_id: entry.id, label: row.char ?? (row.kind === 1 ? whole.label ?? '' : ''),
      origin: row.kind === 1 ? 'corpus' : 'local', production: row.production ?? 'unknown',
      category: name('category', row.category), style: name('style', row.style), state: name('state', row.state),
      revision: row.revision, cut: row.cut, page_id: row.page_key, document: row.document, year: row.year, withheld: row.quiz === 0, flags: row.flags });
  }
  return items;
}
