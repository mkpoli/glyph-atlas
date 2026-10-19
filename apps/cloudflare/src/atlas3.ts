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
// Crockford reads as digits (i and l as 1, o as 0).
const DIGITS = '0123456789abcdefghjkmnpqrstvwxyz';
export function publicId(id: number): string {
  let out = '';
  do { out = DIGITS[id % 32] + out; id = Math.floor(id / 32); } while (id > 0);
  return out;
}
export function cropOf(text: string): number | null {
  if (!/^[0-9a-z]{1,11}$/i.test(text)) return null;
  let id = 0;
  for (const c of text.toLowerCase().replace(/[il]/g, '1').replace(/o/g, '0')) {
    const digit = DIGITS.indexOf(c);
    if (digit < 0) return null;
    id = id * 32 + digit;
  }
  return Number.isSafeInteger(id) && pageOf(id) > 0 ? id : null;
}

// A page bundle: the page's facts and one entry per crop, by slot. Bundles are immutable under their
// revision, so an isolate keeps the last few it read.
export type Entry = Json & { slot: number; id: string };
export type Bundle = { key: string; book: number; rev: number; page: Json | null; bySlot: Map<number, Entry> };
export const bundleKey = (page: number, rev: number) => `v3/pages/${page}-${rev}.json.gz`;
const BUNDLES_HELD = 64;
const held = new Map<string, Promise<Bundle | null>>();
export function bundle(env: Env, page: number, rev: number): Promise<Bundle | null> {
  const key = bundleKey(page, rev), hit = held.get(key);
  if (hit) { held.delete(key); held.set(key, hit); return hit; }
  const read = (async () => {
    const object = await env.MEDIA.get(key);
    if (!object) return null;
    // Bundles are stored gzipped and served with that encoding; the binding hands back the stored bytes.
    const body = await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).json() as Json;
    return { key: body.key, book: body.book, rev: body.rev, page: body.page ?? null,
      bySlot: new Map((body.crops as Entry[]).map(entry => [entry.slot, entry])) };
  })();
  // A failed read is not kept, so the next request reads again.
  read.catch(() => held.delete(key));
  held.set(key, read);
  while (held.size > BUNDLES_HELD) held.delete(held.keys().next().value!);
  return read;
}

// Small code tables (state, style, category), the same for every request until a publication adds a
// code, which a missing code makes the isolate read again.
let codes: Promise<Map<string, string>> | null = null;
async function codeNames(env: Env): Promise<Map<string, string>> {
  codes ??= env.ATLAS.prepare(`SELECT field,code,key FROM codes WHERE field IN ('state','style','category')`)
    .all<{ field: string; code: number; key: string }>()
    .then(r => new Map(r.results.map(row => [`${row.field}:${row.code}`, row.key])));
  codes.catch(() => { codes = null; });
  return codes;
}
async function codeName(env: Env, field: string, code: number | null): Promise<string | null> {
  if (code === null) return null;
  const name = (await codeNames(env)).get(`${field}:${code}`);
  if (name !== undefined) return name;
  codes = null;
  return (await codeNames(env)).get(`${field}:${code}`) ?? null;
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

// A corpus glyph's bundle entry points at its record in a pack (`record_place`: object, offset,
// size); the records of one listing are read in parallel, one range each.
async function packRecord(env: Env, [object, offset, size]: [string, number, number]): Promise<Json | null> {
  const found = await env.MEDIA.get(object, { range: { offset, length: size } });
  if (!found) return null;
  try { return JSON.parse(await found.text()); } catch { return null; }
}

// Fields only a crop's inspector reads, and where the images sit in their packs, which only the image
// route reads.
const UNLISTED = ['text', 'line', 'context_image', 'context_box', 'crop_box', 'image_place', 'context_image_place', 'record_place', 'slot'];
// The item a listing shows for each crop, in the order asked: the bundle's record under the row's
// current values. A crop with no row, a candidate, or one its bundle does not hold is left out.
export async function cropItems(env: Env, ids: number[]): Promise<Json[]> {
  if (!ids.length) return [];
  const rows = (await env.ATLAS.prepare(cropRowsQuery()).bind(JSON.stringify(ids)).all<CropRow>()).results;
  const byId = new Map(rows.map(row => [row.id, row]));
  const pages = new Map(rows.map(row => [pageOf(row.id), row.rev]));
  const bundles = new Map(await Promise.all([...pages].map(async ([page, rev]) => [page, await bundle(env, page, rev)] as const)));
  const entries = new Map(ids.map(id => [id, byId.has(id) ? bundles.get(pageOf(id))?.bySlot.get(slotOf(id)) : undefined]));
  const records = new Map(await Promise.all([...entries].filter(([, entry]) => entry?.record_place)
    .map(async ([id, entry]) => [id, await packRecord(env, entry!.record_place)] as const)));
  const items: Json[] = [];
  for (const id of ids) {
    const row = byId.get(id), entry = entries.get(id);
    if (!row || !entry || row.kind === 2 || entry.retired) continue;
    // A corpus glyph whose record cannot be read is left out.
    const whole = entry.record_place ? records.get(id) : entry;
    if (!whole) continue;
    const record = Object.fromEntries(Object.entries(whole).filter(([k]) => !UNLISTED.includes(k)));
    items.push({ ...record, id: publicId(id), source_id: entry.id, label: row.char ?? '',
      origin: row.kind === 1 ? 'corpus' : 'local', production: row.production ?? 'unknown',
      category: await codeName(env, 'category', row.category), style: await codeName(env, 'style', row.style),
      state: await codeName(env, 'state', row.state), revision: row.revision, cut: row.cut, page_id: row.page_key, document: row.document,
      year: row.year, withheld: row.quiz === 0, flags: row.flags });
  }
  return items;
}
