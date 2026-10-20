// Images on the new storage layout: `/atlas/media/<key>.webp` from the image key map in R2
// (scripts/atlas3/media_map.py), with no D1 read.

// A shard maps the rest of each key, after its first three hex digits, to [object index, offset, size]
// and a content type when it is not WebP. Shards only grow, so a held copy may lack an image published
// since it was read: a key it lacks sends one read straight to R2 before the answer is 404.
type Shard = { objects: string[]; keys: Record<string, [number, number, number, string?]> };
export type Place = { object: string; offset: number; size: number; type: string };
const SHARD_SECONDS = 300, SHARDS_HELD = 32, RECHECK_SECONDS = 30;
// A mapped image whose pack is not in R2: the publication stopped between pack and map.
export class MediaIncomplete extends Error {}
export const shardKey = (key: string) => `v3/media/${key.slice(0, 3)}.json.gz`;

// The edge copy is keyed by a URL on this site that no route answers, and says when it expires.
const edgeKey = (name: string) => new Request(`https://glyphatlas.org/_shards/${name}`);
const EXPIRES = 'x-shard-expires';
type Read<T> = { shard: T | null; expires: number };

async function decoded<T>(bytes: ArrayBuffer): Promise<T> {
  return await new Response(new Response(bytes).body!.pipeThrough(new DecompressionStream('gzip'))).json() as T;
}

const settle = (put: Promise<unknown>, ctx?: ExecutionContext) => ctx ? (ctx.waitUntil(put), undefined) : put;

// A shard from the edge copy, or from R2 when there is none, it has expired, or when `direct`.
async function readShard<T>(env: Env, name: string, now: number, direct: boolean, ctx?: ExecutionContext): Promise<Read<T>> {
  const cacheKey = edgeKey(name);
  if (!direct) {
    const cached = await caches.default.match(cacheKey);
    const expires = Number(cached?.headers.get(EXPIRES));
    if (cached && expires > now) return { shard: await decoded<T>(await cached.arrayBuffer()), expires };
  }
  const bytes = await (await env.MEDIA.get(name))?.arrayBuffer() ?? null;
  const expires = now + SHARD_SECONDS * 1000;
  if (!bytes) return { shard: null, expires };
  const shard = await decoded<T>(bytes);
  await settle(caches.default.put(cacheKey, new Response(bytes, { headers: {
    'cache-control': `public, max-age=${SHARD_SECONDS}`, [EXPIRES]: String(expires) } })), ctx);
  return { shard, expires };
}

// A reader of gzipped JSON shards in R2 MEDIA that answers what `find` takes from one, or null. The
// isolate keeps the last SHARDS_HELD shards it read until their edge copy expires, at most SHARD_SECONDS.
// When `find` finds nothing, the shard is read once more straight from R2, at most once per shard per
// RECHECK_SECONDS in this isolate, and that copy replaces both held ones.
export function heldShards<T>() {
  const held = new Map<string, { expires: number; read: Promise<Read<T>> }>();
  const rechecked = new Map<string, { at: number; done: Promise<unknown> }>();
  const hold = (env: Env, name: string, now: number, direct: boolean, ctx?: ExecutionContext) => {
    const entry = { expires: Infinity, read: readShard<T>(env, name, now, direct, ctx) };
    entry.read.then(read => { entry.expires = read.expires; }, () => { if (held.get(name) === entry) held.delete(name); });
    held.delete(name);
    held.set(name, entry);
    while (held.size > SHARDS_HELD) held.delete(held.keys().next().value!);
    return entry;
  };
  return async <R>(env: Env, name: string, find: (shard: T) => R | undefined, ctx?: ExecutionContext,
    now = Date.now()): Promise<R | null> => {
    let entry = held.get(name);
    if (entry && now < entry.expires) { held.delete(name); held.set(name, entry); }
    else entry = hold(env, name, now, false, ctx);
    const { shard } = await entry.read;
    const found = shard ? find(shard) : undefined;
    if (found !== undefined) return found;
    const last = rechecked.get(name);
    if (last && now - last.at < RECHECK_SECONDS * 1000) await last.done.catch(() => {});
    else {
      if (rechecked.size >= 4096) for (const [k, v] of rechecked) if (now - v.at >= RECHECK_SECONDS * 1000) rechecked.delete(k);
      const fresh = hold(env, name, now, true, ctx);
      rechecked.set(name, { at: now, done: fresh.read });
      await fresh.read;
    }
    const again = await held.get(name)?.read.catch(() => null);
    return (again?.shard ? find(again.shard) : undefined) ?? null;
  };
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
  const object = await env.MEDIA.get(place.object, { range: { offset: place.offset, length: place.size } });
  if (!object) throw new MediaIncomplete(`${place.object} is missing`);
  const response = new Response(object.body, { headers: { 'content-type': place.type, 'content-length': String(place.size),
    'cache-control': 'public, max-age=31536000, immutable', 'etag': `"${key}"`, 'x-content-type-options': 'nosniff' } });
  ctx.waitUntil(caches.default.put(request, response.clone()));
  return response;
}
