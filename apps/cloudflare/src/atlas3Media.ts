// Images on the new storage layout: `/atlas/media/<key>.webp` from the image key map in R2
// (scripts/atlas3/media_map.py), with no D1 read.

// A shard maps the rest of each key, after its first three hex digits, to [object index, offset, size]
// and a content type when it is not WebP. Shards only grow, so a copy may lack an image published in
// the last few minutes: the edge and an isolate keep a shard SHARD_SECONDS, an isolate the last
// SHARDS_HELD.
type Shard = { objects: string[]; keys: Record<string, [number, number, number, string?]> };
export type Place = { object: string; offset: number; size: number; type: string };
const SHARD_SECONDS = 300, SHARDS_HELD = 32;
const held = new Map<string, { at: number; read: Promise<Shard | null> }>();
export const shardKey = (key: string) => `v3/media/${key.slice(0, 3)}.json.gz`;

async function readShard(env: Env, name: string, ctx?: ExecutionContext): Promise<Shard | null> {
  // The edge copy is keyed by a URL on this site that no route answers.
  const cacheKey = new Request(`https://glyphatlas.org/_shards/${name}`);
  let bytes: ArrayBuffer | null = null;
  const cached = await caches.default.match(cacheKey);
  if (cached) bytes = await cached.arrayBuffer();
  else {
    const object = await env.MEDIA.get(name);
    if (!object) return null;
    bytes = await object.arrayBuffer();
    const copy = new Response(bytes, { headers: { 'cache-control': `public, max-age=${SHARD_SECONDS}` } });
    const put = caches.default.put(cacheKey, copy);
    if (ctx) ctx.waitUntil(put); else await put;
  }
  return await new Response(new Response(bytes).body!.pipeThrough(new DecompressionStream('gzip'))).json() as Shard;
}

export async function mediaPlace(env: Env, key: string, ctx?: ExecutionContext, now = Date.now()): Promise<Place | null> {
  if (!/^[a-f0-9]{64}$/.test(key)) return null;
  const name = shardKey(key);
  let entry = held.get(name);
  if (entry && now - entry.at < SHARD_SECONDS * 1000) { held.delete(name); held.set(name, entry); }
  else {
    const fresh = { at: now, read: readShard(env, name, ctx) };
    fresh.read.catch(() => { if (held.get(name) === fresh) held.delete(name); });
    held.delete(name);
    held.set(name, fresh);
    while (held.size > SHARDS_HELD) held.delete(held.keys().next().value!);
    entry = fresh;
  }
  const shard = await entry.read, found = shard?.keys[key.slice(3)];
  if (!shard || !found) return null;
  const [index, offset, size, type] = found;
  return { object: shard.objects[index], offset, size, type: type ?? 'image/webp' };
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
  if (!object) throw new Error(`${place.object} is missing`);
  const response = new Response(object.body, { headers: { 'content-type': place.type, 'content-length': String(place.size),
    'cache-control': 'public, max-age=31536000, immutable', 'etag': `"${key}"`, 'x-content-type-options': 'nosniff' } });
  ctx.waitUntil(caches.default.put(request, response.clone()));
  return response;
}
