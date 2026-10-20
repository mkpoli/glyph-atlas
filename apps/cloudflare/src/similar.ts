// Similar crops: the neighbour lists `atlas similar neighbours` computes are published to R2 by
// scripts/publish_similar.sh as gzipped shards, `similar/<revision>/<shard>.json.gz`, where a crop's
// shard is the first `shard_digits` hex digits of the SHA-256 of its id. `similar/current.json`
// names the revision the site reads. The lists name crops by the text ids they had when the lists
// were made: a crop is found by its text id in the alias map, and is its bundle entry's `id`. A
// neighbour the site no longer holds is left out.
import { bundle, pageOf, slotOf } from './atlas3';
import { aliasCrops } from './atlas3Aliases';

type Json = Record<string, any>;
export type Neighbour = [string, number];
type Entry = { similar?: Neighbour[]; filed_differently?: Neighbour[] };
type Pointer = { revision: string; shard_digits: number };
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
  pointer = { revision: value.revision, shard_digits: value.shard_digits, read: Date.now() };
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

/** The neighbour lists of the crop whose text id is `id`; null names no crop, and has none. */
export async function similarCrops(env: Env, id: string | null, limit: number, items: Items, ctx?: ExecutionContext): Promise<Json> {
  const empty = { revision: null, similar: [], filed_differently: [] };
  const at = await current(env);
  if (!at) return empty;
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

/** Listing items for old text ids, keyed by text id. A text id is the crop it names only when that
 *  crop's entry carries it, so a redirect source, which named no crop of its own, is left out as
 *  before. One alias shard read per shard and one `items` call for the batch. */
export async function itemsByTextId(env: Env, textIds: string[], items: Items, ctx?: ExecutionContext): Promise<Map<string, Json>> {
  const crops = await aliasCrops(env, textIds, ctx);
  const ids = [...new Set(crops.values())];
  const found = new Map<string, Json>();
  for (const item of ids.length ? await items(env, ids) : [])
    if (crops.get(item.source_id) !== undefined) found.set(item.source_id, item);
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
    const found = wanted.length ? await itemsByTextId(env, wanted, items, ctx) : new Map();
    for (const n of wanted) known.set(n, found.get(n) ?? null);
    for (const [n, score] of batch) {
      const item = known.get(n);
      if (item && keep(item) && shown.length < limit) shown.push({ ...item, score });
    }
  }
  return shown;
}
