import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { StorageUnavailable } from './busy';
import { heldShards, mediaPlace, mediaResponse, rereadBudget, shardKey } from './atlas3Media';
import worker from './index';

// The Workers cache, as far as these reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  delete: async (request: Request) => edge.delete(request.url),
} };
beforeEach(() => edge.clear());

let n = 0;
function setup() {
  // Each test takes its own prefix, since shards are held per isolate.
  const prefix = (0x100 + ++n).toString(16), key = prefix + 'a'.repeat(61), png = prefix + 'b'.repeat(61);
  const pack = new Uint8Array(64).map((_, i) => i);
  const reads: string[] = [], failing = new Set<string>();
  const objects = new Map<string, Uint8Array>([
    [shardKey(key), gzipSync(JSON.stringify({ objects: ['packs/p.bin'], keys: { ['a'.repeat(61)]: [0, 8, 4], ['b'.repeat(61)]: [0, 16, 2, 'image/png'] } }))],
    ['packs/p.bin', pack]]);
  const MEDIA = { get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    reads.push(name);
    if (failing.has(name)) throw new Error('R2 is unavailable');
    const bytes = objects.get(name);
    if (!bytes) return null;
    const body = options?.range ? bytes.slice(options.range.offset, options.range.offset + options.range.length) : bytes;
    // A copy of exactly these bytes: a Buffer's `.buffer` can be its whole shared pool.
    return { body: new Response(body).body!, arrayBuffer: async () => new Uint8Array(body).buffer };
  } };
  const waits: Promise<unknown>[] = [];
  const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p) } as unknown as ExecutionContext;
  return { env: { MEDIA } as unknown as Env, key, png, reads, ctx, waits, objects, failing };
}

describe('new-layout images', () => {
  it('find an image by its key in its shard', async () => {
    const { env, key, png } = setup();
    expect(await mediaPlace(env, key)).toEqual({ object: 'packs/p.bin', offset: 8, size: 4, type: 'image/webp' });
    expect((await mediaPlace(env, png))?.type).toBe('image/png');
    expect(await mediaPlace(env, key.slice(0, 63) + 'c')).toBeNull();
    expect(await mediaPlace(env, 'not-a-key')).toBeNull();
  });

  it('serve the bytes immutable under their key, and answer a matching ETag with 304', async () => {
    const { env, key, ctx, waits } = setup();
    const url = `https://glyphatlas.org/atlas/media/${key}.webp`;
    const response = (await mediaResponse(env, new Request(url), key, ctx))!;
    expect([...new Uint8Array(await response.arrayBuffer())]).toEqual([8, 9, 10, 11]);
    expect(response.headers.get('cache-control')).toBe('public, max-age=31536000, immutable');
    expect(response.headers.get('etag')).toBe(`"${key}"`);
    await Promise.all(waits);
    edge.delete(url);
    expect((await mediaResponse(env, new Request(url, { headers: { 'if-none-match': `"${key}"` } }), key, ctx))!.status).toBe(304);
    expect(await mediaResponse(env, new Request(url + 'x'), key.slice(0, 63) + 'c', ctx)).toBeNull();
  });

  it('read a shard from R2 once, then from the isolate and the edge', async () => {
    const { env, key, reads } = setup();
    await mediaPlace(env, key);
    await mediaPlace(env, key);
    expect(reads.filter(name => name === shardKey(key))).toHaveLength(1);
    expect(edge.has(`https://glyphatlas.org/_shards/${shardKey(key)}`)).toBe(true);
    // Five minutes on, the isolate reads it again, from the edge.
    edge.clear();
    await mediaPlace(env, key, undefined, Date.now() + 301_000);
    expect(reads.filter(name => name === shardKey(key))).toHaveLength(2);
  });

  it('serve a key published after the shard was read, from one read straight to R2', async () => {
    const { env, key, reads, objects } = setup(), name = shardKey(key), later = key.slice(0, 3) + 'd'.repeat(61);
    const now = Date.now();
    await mediaPlace(env, key, undefined, now);
    objects.set(name, gzipSync(JSON.stringify({ objects: ['packs/p.bin'], keys: { ['a'.repeat(61)]: [0, 8, 4], ['d'.repeat(61)]: [0, 24, 3] } })));
    expect(await mediaPlace(env, later, undefined, now + 31_000)).toEqual({ object: 'packs/p.bin', offset: 24, size: 3, type: 'image/webp' });
    expect(reads.filter(r => r === name)).toHaveLength(2);
    // The edge copy is the new one, and the isolate holds it.
    expect(await mediaPlace(env, later, undefined, now + 32_000)).not.toBeNull();
    expect(reads.filter(r => r === name)).toHaveLength(2);
  });

  it('read a shard straight from R2 for missing keys only when the held copy is 30 seconds old', async () => {
    const { env, key, reads } = setup(), name = shardKey(key), bogus = (c: string) => key.slice(0, 3) + c.repeat(61);
    const now = Date.now();
    await mediaPlace(env, key, undefined, now);
    expect(await mediaPlace(env, bogus('c'), undefined, now + 1000)).toBeNull();
    expect(reads.filter(r => r === name)).toHaveLength(1);
    // Misses together share one read.
    expect(await Promise.all(['c', 'e', 'f'].map(c => mediaPlace(env, bogus(c), undefined, now + 31_000)))).toEqual([null, null, null]);
    expect(reads.filter(r => r === name)).toHaveLength(2);
    expect(await mediaPlace(env, bogus('9'), undefined, now + 50_000)).toBeNull();
    expect(reads.filter(r => r === name)).toHaveLength(2);
    expect(await mediaPlace(env, bogus('9'), undefined, now + 62_000)).toBeNull();
    expect(reads.filter(r => r === name)).toHaveLength(3);
  });

  it('hold a shard read from the edge only until the edge copy expires', async () => {
    const { env, key, reads, objects } = setup(), name = shardKey(key), now = Date.now();
    edge.set(`https://glyphatlas.org/_shards/${name}`, new Response(objects.get(name)!.slice(), { headers: { 'x-shard-expires': String(now + 60_000) } }));
    await mediaPlace(env, key, undefined, now);
    expect(reads.filter(r => r === name)).toHaveLength(0);
    await mediaPlace(env, key, undefined, now + 61_000);
    expect(reads.filter(r => r === name)).toHaveLength(1);
  });
});

describe('shards that fail', () => {
  it('replace an edge copy that does not decode with the copy in R2', async () => {
    const { env, key, reads } = setup(), name = shardKey(key), url = `https://glyphatlas.org/_shards/${name}`;
    edge.set(url, new Response('not gzip', { headers: { 'x-shard-expires': String(Date.now() + 60_000) } }));
    expect(await mediaPlace(env, key)).not.toBeNull();
    expect(reads.filter(r => r === name)).toHaveLength(1);
    expect(edge.get(url)!.headers.get('x-shard-expires')).not.toBeNull();
    const { gunzipSync } = await import('node:zlib');
    expect(JSON.parse(gunzipSync(new Uint8Array(await edge.get(url)!.arrayBuffer())).toString()).objects).toEqual(['packs/p.bin']);
  });

  it('keep no edge copy of a shard in R2 that does not decode', async () => {
    const { env, key, objects } = setup(), name = shardKey(key);
    objects.set(name, gzipSync('{"objects": ['));
    await expect(mediaPlace(env, key)).rejects.toMatchObject({ status: 503, where: `${name} does not decode` });
    expect(edge.has(`https://glyphatlas.org/_shards/${name}`)).toBe(false);
    // The next request reads R2 again and finds the repaired shard.
    objects.set(name, gzipSync(JSON.stringify({ objects: ['packs/p.bin'], keys: { ['a'.repeat(61)]: [0, 8, 4] } })));
    expect(await mediaPlace(env, key)).not.toBeNull();
  });
});

// Shards named `s<i>`, each mapping `k<i>` to i, read through their own reader and budget.
function shardSet(count: number) {
  const objects = new Map<string, Uint8Array>(), reads: string[] = [], failing = new Set<string>();
  const gates = new Map<string, Promise<void>>();
  for (let i = 0; i < count; i++) objects.set(`s${i}`, gzipSync(JSON.stringify({ [`k${i}`]: i })));
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    await gates.get(name);
    if (failing.has(name)) throw new Error('R2 is unavailable');
    const bytes = objects.get(name);
    return bytes ? { arrayBuffer: async () => new Uint8Array(bytes).buffer } : null;
  } };
  const find = (key: string) => (shard: Record<string, number>) => Object.hasOwn(shard, key) ? shard[key] : undefined;
  return { env: { MEDIA } as unknown as Env, objects, reads, failing, gates, find };
}

describe('reads straight to R2', () => {
  it('stay within the budget when bogus keys arrive for many shards at once', async () => {
    const { env, reads, find } = shardSet(60), lookup = heldShards<Record<string, number>>(rereadBudget(2, 4));
    const now = Date.now(), names = Array.from({ length: 30 }, (_, i) => `s${i}`);
    await Promise.all(names.map(name => lookup(env, name, find('nothing'), undefined, now)));
    expect(reads).toHaveLength(30);
    // A minute on, every held copy is old enough to read again, but the bucket holds four reads.
    const answers = await Promise.all(names.flatMap(name => [1, 2, 3].map(j => lookup(env, name, find(`bogus${j}`), undefined, now + 60_000))));
    expect(answers.every(answer => answer === null)).toBe(true);
    expect(reads).toHaveLength(34);
    // Half a second refills one read, and keys a held copy has are answered without any.
    await Promise.all(names.slice(10).map(name => lookup(env, name, find('bogus'), undefined, now + 60_500)));
    expect(reads).toHaveLength(35);
    expect(await lookup(env, 's29', find('k29'), undefined, now + 60_500)).toBe(29);
    expect(reads).toHaveLength(35);
  });

  it('answer every request waiting on a read that fails with StorageUnavailable, and try again on the next miss', async () => {
    const { env, objects, reads, failing, find } = shardSet(1), lookup = heldShards<Record<string, number>>(rereadBudget(100, 100));
    const now = Date.now();
    expect(await lookup(env, 's0', find('k0'), undefined, now)).toBe(0);
    objects.set('s0', gzipSync(JSON.stringify({ k0: 0, later: 9 })));
    failing.add('s0');
    const waiting = await Promise.allSettled([1, 2, 3].map(() => lookup(env, 's0', find('later'), undefined, now + 31_000)));
    expect(reads).toHaveLength(2);
    for (const result of waiting) {
      expect(result.status).toBe('rejected');
      expect((result as PromiseRejectedResult).reason).toBeInstanceOf(StorageUnavailable);
    }
    // The held copy still answers what it has.
    expect(await lookup(env, 's0', find('k0'), undefined, now + 31_100)).toBe(0);
    failing.delete('s0');
    expect(await lookup(env, 's0', find('later'), undefined, now + 31_200)).toBe(9);
    expect(reads).toHaveLength(3);
  });

  it('answer from the copy they read even when the isolate has dropped it since', async () => {
    const { env, objects, gates, find } = shardSet(40), lookup = heldShards<Record<string, number>>(rereadBudget(100, 100));
    const now = Date.now();
    await lookup(env, 's0', find('k0'), undefined, now);
    objects.set('s0', gzipSync(JSON.stringify({ k0: 0, later: 9 })));
    let open = () => {};
    gates.set('s0', new Promise<void>(done => { open = done; }));
    const waiting = lookup(env, 's0', find('later'), undefined, now + 31_000);
    // Thirty-nine other shards push s0 out of the isolate while its read is under way.
    await Promise.all(Array.from({ length: 39 }, (_, i) => lookup(env, `s${i + 1}`, find('x'), undefined, now + 31_000)));
    open();
    expect(await waiting).toBe(9);
  });
});

// The route itself, with no `DB` binding: a read of the old database would fail the request.
describe('the image route', () => {
  const get = (env: Env, ctx: ExecutionContext, key: string, headers: HeadersInit = {}) =>
    worker.fetch(new Request(`https://glyphatlas.org/atlas/media/${key}.webp`, { headers }), env, ctx);

  it('serves a mapped image immutable under its key, with its content type', async () => {
    const { env, key, png, ctx, waits } = setup();
    const response = await get(env, ctx, key);
    expect(response.status).toBe(200);
    expect([...new Uint8Array(await response.arrayBuffer())]).toEqual([8, 9, 10, 11]);
    expect(Object.fromEntries(response.headers)).toMatchObject({ 'content-type': 'image/webp', 'content-length': '4',
      'cache-control': 'public, max-age=31536000, immutable', 'etag': `"${key}"`, 'x-content-type-options': 'nosniff' });
    expect((await get(env, ctx, png)).headers.get('content-type')).toBe('image/png');
    await Promise.all(waits);
    expect(edge.has(`https://glyphatlas.org/atlas/media/${key}.webp`)).toBe(true);
  });

  it('answers a matching ETag with 304', async () => {
    const { env, key, ctx } = setup();
    expect((await get(env, ctx, key, { 'if-none-match': `"${key}"` })).status).toBe(304);
  });

  it('answers 404 for a key the map does not hold, and for a key in a shard not yet published', async () => {
    const { env, key, ctx } = setup();
    const missing = await get(env, ctx, key.slice(0, 63) + 'c');
    expect(missing.status).toBe(404);
    expect(await missing.json()).toEqual({ detail: 'Image not found.' });
    expect((await get(env, ctx, 'fff' + 'a'.repeat(61))).status).toBe(404);
  });

  it('answers 503 when a mapped pack is missing', async () => {
    const { env, key, ctx } = setup();
    const broken = { MEDIA: { get: async (name: string, options?: object) => options ? null : (env as any).MEDIA.get(name) } } as unknown as Env;
    const response = await get(broken, ctx, key);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: 'Image publication is incomplete.' });
  });

  it('answers 503 busy with retry-after when R2 fails or a shard does not decode', async () => {
    for (const fault of ['shard', 'corrupt', 'pack'] as const) {
      const { env, key, ctx, failing, objects } = setup();
      if (fault === 'shard') failing.add(shardKey(key));
      if (fault === 'corrupt') objects.set(shardKey(key), new Uint8Array([1, 2, 3]));
      if (fault === 'pack') failing.add('packs/p.bin');
      const response = await get(env, ctx, key);
      expect(response.status).toBe(503);
      expect(response.headers.get('retry-after')).toBe('5');
      expect(await response.json()).toEqual({ detail: 'Storage is unavailable. Try again in a moment.', code: 'busy' });
    }
  });
});
