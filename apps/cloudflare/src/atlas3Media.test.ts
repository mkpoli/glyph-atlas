import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { mediaPlace, mediaResponse, shardKey } from './atlas3Media';
import worker from './index';

// The Workers cache, as far as these reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());

let n = 0;
function setup() {
  // Each test takes its own prefix, since shards are held per isolate.
  const prefix = (0x100 + ++n).toString(16), key = prefix + 'a'.repeat(61), png = prefix + 'b'.repeat(61);
  const pack = new Uint8Array(64).map((_, i) => i);
  const reads: string[] = [];
  const objects = new Map<string, Uint8Array>([
    [shardKey(key), gzipSync(JSON.stringify({ objects: ['packs/p.bin'], keys: { ['a'.repeat(61)]: [0, 8, 4], ['b'.repeat(61)]: [0, 16, 2, 'image/png'] } }))],
    ['packs/p.bin', pack]]);
  const MEDIA = { get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    reads.push(name);
    const bytes = objects.get(name);
    if (!bytes) return null;
    const body = options?.range ? bytes.slice(options.range.offset, options.range.offset + options.range.length) : bytes;
    // A copy of exactly these bytes: a Buffer's `.buffer` can be its whole shared pool.
    return { body: new Response(body).body!, arrayBuffer: async () => new Uint8Array(body).buffer };
  } };
  const waits: Promise<unknown>[] = [];
  const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p) } as unknown as ExecutionContext;
  return { env: { MEDIA } as unknown as Env, key, png, reads, ctx, waits };
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
});
