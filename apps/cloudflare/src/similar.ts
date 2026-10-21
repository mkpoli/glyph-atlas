// Similar crops: the neighbour lists `atlas similar neighbours` computes are published to R2 by
// scripts/publish_similar.sh as gzipped shards, `similar/<revision>/<shard>.json.gz`, where a crop's
// shard is the first `shard_digits` hex digits of the SHA-256 of its id. `similar/current.json`
// names the revision the site reads. New lists use public crop ids. Older lists use source ids,
// resolved through the frozen alias map and checked against each crop's bundle entry. A neighbour
// the site no longer holds is left out.
import { bundle, cropOf, pageOf, publicId, slotOf } from './atlas3';
import { aliasCrops } from './atlas3Aliases';

type Json = Record<string, any>;
export type Neighbour = [string, number];
type Entry = { similar?: Neighbour[]; filed_differently?: Neighbour[] };
type Pointer = { revision: string; shard_digits: number; id_format?: 'public' };
/** Listing items for crops by integer id, in the order asked; a crop that has none is left out. */
export type Items = (env: Env, ids: number[]) => Promise<Json[]>;

// The pointer changes only when a new revision is published; an isolate rereads it after this long.
const POINTER_SECONDS = 300;
let pointer: (Pointer & { read: number }) | null = null;

async function current(env: Env): Promise<Pointer | null> {
  if (pointer && Date.now() - pointer.read < POINTER_SECONDS * 1000) return pointer;
  const object = await env.MEDIA.get('similar/current.json');
  if (!object) return null;
  const value = await object.json<Pointer>();
  pointer = { ...value, read: Date.now() };
  return pointer;
}

export function resetPointer() {
  pointer = null;
}

export async function shardOf(id: string, digits: number): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(id)));
  return [...digest].map(b => b.toString(16).padStart(2, '0')).join('').slice(0, digits);
}

// A crop's page revision, a primary-key read; the crop's text id is its entry's `id` in that bundle.
export const pageRevQuery = () => 'SELECT rev FROM pages WHERE id=?';

/** The text id a crop had at the cutover, which its neighbour lists are keyed by, or null. */
export async function textIdOf(env: Env, crop: number): Promise<string | null> {
  const row = await env.ATLAS.prepare(pageRevQuery()).bind(pageOf(crop)).first<{ rev: number }>();
  if (!row) return null;
  const entry = (await bundle(env, pageOf(crop), row.rev))?.bySlot.get(slotOf(crop));
  return typeof entry?.id === 'string' ? entry.id : null;
}

/** Neighbour lists use public crop ids, or the original source id for an older publication. */
export async function similarCrops(env: Env, crop: number | null, limit: number, items: Items, ctx?: ExecutionContext): Promise<Json> {
  const empty = { revision: null, similar: [], filed_differently: [] };
  const at = await current(env);
  if (!at) return empty;
  const id = crop === null ? null : at.id_format === 'public' ? publicId(crop) : await textIdOf(env, crop);
  if (id === null) return { ...empty, revision: at.revision };
  const object = await env.MEDIA.get(`similar/${at.revision}/${await shardOf(id, at.shard_digits)}.json.gz`);
  if (!object) return { ...empty, revision: at.revision };
  const shard = JSON.parse(await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).text());
  const entry: Entry = shard[id] ?? {};
  const known = new Map<string, Json | null>();
  const similar = await resolveNeighbours(env, entry.similar ?? [], limit, items, known, undefined, ctx);
  return { revision: at.revision, similar,
    filed_differently: await resolveNeighbours(env, entry.filed_differently ?? [], limit, items, known, undefined, ctx) };
}

/** Resolve public crop ids directly and legacy source ids through the frozen alias map.
 * A legacy id must still be the crop's source id; redirect aliases do not add duplicate results. */
export async function itemsById(env: Env, names: string[], items: Items, ctx?: ExecutionContext): Promise<Map<string, Json>> {
  const unique = [...new Set(names)], crops = new Map<string, number>(), legacy: string[] = [];
  for (const name of unique) {
    const crop = cropOf(name);
    if (crop === null) legacy.push(name); else crops.set(name, crop);
  }
  for (const [name, crop] of await aliasCrops(env, legacy, ctx)) crops.set(name, crop);
  const ids = [...new Set(crops.values())];
  const records = new Map((ids.length ? await items(env, ids) : []).map(item => [cropOf(item.id), item]));
  const found = new Map<string, Json>();
  for (const [name, crop] of crops) {
    const item = records.get(crop);
    if (item && (cropOf(name) !== null || name === item.source_id)) found.set(name, item);
  }
  return found;
}

/**
 * The first `limit` crops of a neighbour list that the site holds, with their scores. The list is
 * resolved in order, `limit` ids at a time: a corpus glyph costs a record read, so the crops past what
 * is shown are never looked up. `known` carries the lookups between the lists of one answer; `keep`
 * leaves out a crop the caller does not want, such as one relabelled since the list was made.
 */
export async function resolveNeighbours(env: Env, list: Neighbour[], limit: number, items: Items,
  known: Map<string, Json | null>, keep: (item: Json) => boolean = () => true, ctx?: ExecutionContext): Promise<Json[]> {
  const shown: Json[] = [];
  for (let start = 0; start < list.length && shown.length < limit; start += limit) {
    const batch = list.slice(start, start + limit);
    const wanted = batch.map(([n]) => n).filter(n => !known.has(n));
    const found = wanted.length ? await itemsById(env, wanted, items, ctx) : new Map();
    for (const n of wanted) known.set(n, found.get(n) ?? null);
    for (const [n, score] of batch) {
      const item = known.get(n);
      if (item && keep(item) && shown.length < limit) shown.push({ ...item, score });
    }
  }
  return shown;
}
