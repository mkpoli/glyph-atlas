import { readRecords, recordKey } from './records';

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
const BUNDLES_HELD = 64, BUNDLE_EDGE_SECONDS = 86400;
const held = new Map<string, Promise<Bundle | null>>();
export function bundle(env: Env, page: number, rev: number): Promise<Bundle | null> {
  const key = bundleKey(page, rev), hit = held.get(key);
  if (hit) { held.delete(key); held.set(key, hit); return hit; }
  const read = (async () => {
    const edge = new Request(`https://glyphatlas.org/_bundles/${key}`);
    let bytes = await caches.default.match(edge).then(hit => hit?.arrayBuffer() ?? null);
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
// bundle is not read.
export async function cropItems(env: Env, ids: number[], given: Map<number, Entry> = new Map()): Promise<Json[]> {
  if (!ids.length) return [];
  const rows = (await env.ATLAS.prepare(cropRowsQuery()).bind(JSON.stringify(ids)).all<CropRow>()).results;
  const byId = new Map(rows.map(row => [row.id, row]));
  const pages = new Map(rows.filter(row => !given.has(row.id)).map(row => [pageOf(row.id), row.rev]));
  const bundles = new Map(await Promise.all([...pages].map(async ([page, rev]) => [page, await bundle(env, page, rev)] as const)));
  const entries = new Map(ids.map(id => [id, !byId.has(id) ? undefined : given.get(id) ?? bundles.get(pageOf(id))?.bySlot.get(slotOf(id))]));
  const pointer = (entry: Entry) => ({ object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] });
  const records = await readRecords(env, [...entries.values()].filter(entry => entry?.record_place).map(entry => pointer(entry!)));
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
