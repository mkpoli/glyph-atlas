// Similar crops: the neighbour lists `atlas similar neighbours` computes are published to R2 by
// scripts/publish_similar.sh as gzipped shards, `similar/<revision>/<shard>.json.gz`, where a crop's
// shard is the first `shard_digits` hex digits of the SHA-256 of its id. `similar/current.json`
// names the revision the site reads. A neighbour the site no longer holds is left out.
type Json = Record<string, any>;
type Neighbour = [string, number];
type Entry = { similar?: Neighbour[]; filed_differently?: Neighbour[] };
type Pointer = { revision: string; shard_digits: number };
export type ItemsFor = (env: Env, ids: string[]) => Promise<Map<string, Json>>;

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

export async function similarCrops(env: Env, id: string, limit: number, itemsFor: ItemsFor): Promise<Json> {
  const empty = { revision: null, similar: [], filed_differently: [] };
  const at = await current(env);
  if (!at) return empty;
  const object = await env.MEDIA.get(`similar/${at.revision}/${await shardOf(id, at.shard_digits)}.json.gz`);
  if (!object) return { ...empty, revision: at.revision };
  const shard = JSON.parse(await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).text());
  const entry: Entry = shard[id] ?? {};
  // Each list is resolved in order, `limit` ids at a time, until it has `limit` crops the site holds:
  // a corpus glyph costs a record read, so the crops past what is shown are never looked up.
  const known = new Map<string, Json | null>();
  async function pick(list: Neighbour[]) {
    const shown: Json[] = [];
    for (let start = 0; start < list.length && shown.length < limit; start += limit) {
      const batch = list.slice(start, start + limit);
      const wanted = batch.map(([n]) => n).filter(n => !known.has(n));
      const found = wanted.length ? await itemsFor(env, wanted) : new Map();
      for (const n of wanted) known.set(n, found.get(n) ?? null);
      for (const [n, score] of batch) {
        const item = known.get(n);
        if (item && shown.length < limit) shown.push({ ...item, score });
      }
    }
    return shown;
  }
  const similar = await pick(entry.similar ?? []);
  return { revision: at.revision, similar, filed_differently: await pick(entry.filed_differently ?? []) };
}
