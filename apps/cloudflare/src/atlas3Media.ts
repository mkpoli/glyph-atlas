import { StorageUnavailable } from './busy';

// Images on the new storage layout: `/atlas/media/<key>.webp` from the image key map in R2
// (scripts/atlas3/media_map.py), with no D1 read.

// A shard maps the rest of each key, after its first three hex digits, to [object index, offset, size]
// and a content type when it is not WebP. Shards only grow, so a held copy may lack an image published
// since it was read: a key it lacks may send one read straight to R2 before the answer is 404.
type Shard = { objects: string[]; keys: Record<string, [number, number, number, string?]> };
export type Place = { object: string; offset: number; size: number; type: string };
const SHARD_SECONDS = 300, SHARDS_HELD = 32, RECHECK_SECONDS = 30;
// Reads straight to R2 for keys a held shard lacks: REREADS a second per isolate, REREAD_BURST at once.
const REREADS = 2, REREAD_BURST = 4;
// A mapped image whose pack is not in R2: the publication stopped between pack and map.
export class MediaIncomplete extends Error {}
export const shardKey = (key: string) => `v3/media/${key.slice(0, 3)}.json.gz`;

// The edge copy is keyed by a URL on this site that no route answers, and says when it expires.
const edgeKey = (name: string) => new Request(`https://glyphatlas.org/_shards/${name}`);
const EXPIRES = 'x-shard-expires';
// `expires` is SHARD_SECONDS after the copy was read from R2.
type Read<T> = { shard: T | null; expires: number };

async function decoded<T>(bytes: ArrayBuffer): Promise<T> {
  const value = await new Response(new Response(bytes).body!.pipeThrough(new DecompressionStream('gzip'))).json();
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('A shard is not a JSON object.');
  return value as T;
}

const settle = (put: Promise<unknown>, ctx?: ExecutionContext) => ctx ? (ctx.waitUntil(put), undefined) : put;

// A shard from the edge copy, or from R2 when there is none, it has expired or it does not decode, or
// when `direct`. A copy read from R2 is decoded before it replaces the edge copy.
async function readShard<T>(env: Env, name: string, now: number, direct: boolean, ctx?: ExecutionContext): Promise<Read<T>> {
  const cacheKey = edgeKey(name);
  if (!direct) {
    const cached = await caches.default.match(cacheKey);
    const expires = Number(cached?.headers.get(EXPIRES));
    if (cached && expires > now) {
      try { return { shard: await decoded<T>(await cached.arrayBuffer()), expires }; }
      catch { await settle(caches.default.delete(cacheKey), ctx); }
    }
  }
  let bytes: ArrayBuffer | null;
  try { bytes = await (await env.MEDIA.get(name))?.arrayBuffer() ?? null; }
  catch (error) { throw new StorageUnavailable(`${name} could not be read`, { cause: error }); }
  const expires = now + SHARD_SECONDS * 1000;
  if (!bytes) return { shard: null, expires };
  let shard: T;
  try { shard = await decoded<T>(bytes); }
  catch (error) { throw new StorageUnavailable(`${name} does not decode`, { cause: error }); }
  await settle(caches.default.put(cacheKey, new Response(bytes, { headers: {
    'cache-control': `public, max-age=${SHARD_SECONDS}`, [EXPIRES]: String(expires) } })), ctx);
  return { shard, expires };
}

// A token bucket of reads straight to R2: whether one more fits at `now`.
export function rereadBudget(rate = REREADS, burst = REREAD_BURST) {
  let tokens = burst, at = -Infinity;
  return (now: number) => {
    tokens = Math.min(burst, tokens + Math.max(0, now - at) * rate / 1000);
    at = now;
    if (tokens < 1) return false;
    tokens -= 1;
    return true;
  };
}
const rereads = rereadBudget();

// A reader of gzipped JSON shards in R2 MEDIA that answers what `find` takes from one, or null. The
// isolate keeps the last `size` shards this reader read until their edge copy expires, at most SHARD_SECONDS.
// When `find` finds nothing in a copy read from R2 at least RECHECK_SECONDS ago, the shard is read once
// more straight from R2 if `budget` allows; requests that miss meanwhile wait for that read. Its copy
// replaces both held ones; when it fails, every waiter gets StorageUnavailable and nothing is kept.
export function heldShards<T>(size = SHARDS_HELD, budget = rereads) {
  type Held = { expires: number; read: Promise<Read<T>> };
  const held = new Map<string, Held>();
  const rereading = new Map<string, Promise<Read<T>>>();
  const keep = (name: string, entry: Held) => {
    held.delete(name);
    held.set(name, entry);
    while (held.size > size) held.delete(held.keys().next().value!);
  };
  const hold = (env: Env, name: string, now: number, ctx?: ExecutionContext) => {
    const entry: Held = { expires: Infinity, read: readShard<T>(env, name, now, false, ctx) };
    entry.read.then(read => { entry.expires = read.expires; }, () => { if (held.get(name) === entry) held.delete(name); });
    keep(name, entry);
    return entry;
  };
  const reread = (env: Env, name: string, now: number, ctx?: ExecutionContext) => {
    const read = readShard<T>(env, name, now, true, ctx);
    rereading.set(name, read);
    read.then(fresh => keep(name, { expires: fresh.expires, read: Promise.resolve(fresh) }), () => {})
      .finally(() => rereading.delete(name));
    return read;
  };
  return async <R>(env: Env, name: string, find: (shard: T) => R | undefined, ctx?: ExecutionContext,
    now = Date.now()): Promise<R | null> => {
    let entry = held.get(name);
    if (entry && now < entry.expires) keep(name, entry);
    else entry = hold(env, name, now, ctx);
    const read = await entry.read;
    const found = read.shard ? find(read.shard) : undefined;
    if (found !== undefined) return found;
    let fresh = rereading.get(name);
    if (!fresh) {
      const age = now - (read.expires - SHARD_SECONDS * 1000);
      if (age < RECHECK_SECONDS * 1000 || !budget(now)) return null;
      fresh = reread(env, name, now, ctx);
    }
    const again = await fresh;
    return (again.shard ? find(again.shard) : undefined) ?? null;
  };
}

// A reader of whole gzipped JSON objects in R2 MEDIA, held as `heldShards` holds them: a missing object
// may send one read straight to R2 before the answer is null.
export function heldObjects<T>(size = SHARDS_HELD, budget = rereads) {
  const read = heldShards<T>(size, budget);
  return (env: Env, name: string, ctx?: ExecutionContext, now = Date.now()) => read(env, name, object => object, ctx, now);
}
const mediaShard = heldShards<Shard>();

export async function mediaPlace(env: Env, key: string, ctx?: ExecutionContext, now = Date.now()): Promise<Place | null> {
  if (!/^[a-f0-9]{64}$/.test(key)) return null;
  const rest = key.slice(3);
  return await mediaShard(env, shardKey(key), shard => {
    if (!Object.hasOwn(shard.keys ?? {}, rest)) return undefined;
    const [index, offset, size, type] = shard.keys[rest];
    return { object: shard.objects[index], offset, size, type: type ?? 'image/webp' };
  }, ctx, now);
}

// The image as the live route answers it: immutable under its key, with its key as the ETag. Null
// when the map does not hold the key.
export async function mediaResponse(env: Env, request: Request, key: string, ctx: ExecutionContext): Promise<Response | null> {
  const cached = await caches.default.match(request);
  if (cached) return cached;
  const place = await mediaPlace(env, key, ctx);
  if (!place) return null;
  if (request.headers.get('if-none-match') === `"${key}"`) return new Response(null, { status: 304 });
  let object: R2ObjectBody | null;
  try { object = await env.MEDIA.get(place.object, { range: { offset: place.offset, length: place.size } }); }
  catch (error) { throw new StorageUnavailable(`${place.object} could not be read`, { cause: error }); }
  if (!object) throw new MediaIncomplete(`${place.object} is missing`);
  const response = new Response(object.body, { headers: { 'content-type': place.type, 'content-length': String(place.size),
    'cache-control': 'public, max-age=31536000, immutable', 'etag': `"${key}"`, 'x-content-type-options': 'nosniff' } });
  ctx.waitUntil(caches.default.put(request, response.clone()));
  return response;
}
