import { describe, expect, it } from 'bun:test';
import { StorageUnavailable } from './busy';
import { composerFile, composerPreflight } from './composer';
import worker from './index';
import { COMPOSER_VERSION } from './composerVersions';

const version = COMPOSER_VERSION, hash = 'ab'.repeat(32), absent = `g/cd/${'cd'.repeat(32)}.json`;
const stored = new Map([[`composer/${version}/b/007.json.gz`, new Uint8Array([0x1f, 0x8b, 8])],
  [`composer/${version}/g/ab/${hash}.json`, new Uint8Array([0x1f, 0x8b, 9])], [`composer/${version}/OFL.txt`, new TextEncoder().encode('SIL')],
  [`composer/${version}/manifest.json`, new TextEncoder().encode('{}')]]);
const shared = (response: Response) => {
  expect(response.headers.get('access-control-allow-origin')).toBe('*');
  expect(response.headers.get('cross-origin-resource-policy')).toBe('cross-origin');
  expect(response.headers.get('vary')).toBeNull();
};
const env = { MEDIA: { async get(key: string) {
  const bytes = stored.get(key);
  return bytes ? { body: new Response(bytes).body!, size: bytes.length, httpEtag: '"e"' } : null;
} } } as any;

describe('composerFile', () => {
  it('serves a stored file of a version for good, the record and host files as bytes', async () => {
    const response = (await composerFile(env, `${version}/b/007.json.gz`))!;
    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([0x1f, 0x8b, 8]));
    expect(response.headers.get('content-type')).toBe('application/octet-stream');
    expect(response.headers.get('cache-control')).toBe('public, max-age=31536000, immutable');
    const nested = (await composerFile(env, `${version}/g/ab/${hash}.json`))!;
    expect(new Uint8Array(await nested.arrayBuffer())).toEqual(new Uint8Array([0x1f, 0x8b, 9]));
    expect(nested.headers.get('content-type')).toBe('application/octet-stream');
    expect(nested.headers.get('content-encoding')).toBeNull();
    expect((await composerFile(env, `${version}/manifest.json`))!.headers.get('content-type')).toBe('application/json');
    expect((await composerFile(env, `${version}/OFL.txt`))!.headers.get('content-type')).toBe('text/plain; charset=utf-8');
  });
  it('answers a file the version lacks as missing, and nothing for a path naming no file or another version', async () => {
    const missing = (await composerFile(env, `${version}/b/008.json.gz`))!;
    expect(missing.status).toBe(404);
    expect(missing.headers.get('cache-control')).toBe('public, max-age=300');
    for (const bad of [`${version}/../x.json`, `${version}/b/x.svg`, `${version}/a/b/c/d/e/f.json`, `${version}/.hidden.json`,
      `${version}/ids.svg`, 'manifest.json', 'c21e0f1670f4-compose-layout-v4-f1/manifest.json', `${version}+dirty/layout.json`])
      expect(await composerFile(env, bad)).toBeNull();
  });
  it('keeps a missing file\'s answer at the edge', async () => {
    const kept = new Map<string, Response>(), waited: Promise<unknown>[] = [];
    (globalThis as any).caches = { default: { match: async (r: Request) => kept.get(r.url)?.clone(), put: async (r: Request, res: Response) => { kept.set(r.url, res); } } };
    try {
      const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as any;
      const url = `https://glyphatlas.org/atlas/composer/${version}/${absent}`;
      expect((await composerFile(env, `${version}/${absent}`, new Request(url), ctx))!.status).toBe(404);
      await Promise.all(waited);
      expect(kept.get(url)?.status).toBe(404);
      // Asked again, the edge answers; R2 is not read.
      const unread = { MEDIA: { get: async () => { throw new Error('R2 was read'); } } } as any;
      expect((await composerFile(unread, `${version}/${absent}`, new Request(url), ctx))!.status).toBe(404);
    } finally { delete (globalThis as any).caches; }
  });
  it('lets any site read a file, a missing one, and an edge copy', async () => {
    shared((await composerFile(env, `${version}/manifest.json`))!);
    shared((await composerFile(env, `${version}/b/008.json.gz`))!);
    const kept = new Map<string, Response>(), waited: Promise<unknown>[] = [];
    (globalThis as any).caches = { default: { match: async (r: Request) => kept.get(r.url)?.clone(), put: async (r: Request, res: Response) => { kept.set(r.url, res); } } };
    try {
      const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as any;
      const url = `https://glyphatlas.org/atlas/composer/${version}/manifest.json`;
      await composerFile(env, `${version}/manifest.json`, new Request(url), ctx);
      await Promise.all(waited);
      shared(kept.get(url)!);
      // A copy kept before the files were shared is answered with the headers all the same.
      const old = `https://glyphatlas.org/atlas/composer/${version}/OFL.txt`;
      kept.set(old, new Response('SIL', { headers: { 'cache-control': 'public, max-age=31536000, immutable' } }));
      const unread = { MEDIA: { get: async () => { throw new Error('R2 was read'); } } } as any;
      const hit = (await composerFile(unread, `${version}/OFL.txt`, new Request(old), ctx))!;
      shared(hit);
      expect(await hit.text()).toBe('SIL');
    } finally { delete (globalThis as any).caches; }
  });
  it('answers another site\'s preflight, and a path naming no file, so it may read them', async () => {
    const preflight = composerPreflight();
    expect(preflight.status).toBe(204);
    shared(preflight);
    expect(preflight.headers.get('access-control-allow-methods')).toBe('GET, HEAD, OPTIONS');
    expect(preflight.headers.get('access-control-max-age')).toBe('86400');
    const routed = await worker.fetch(new Request(`https://glyphatlas.org/atlas/composer/${version}/manifest.json`, {
      method: 'OPTIONS', headers: { origin: 'https://example.org', 'access-control-request-method': 'GET' } }), { WRITES_PAUSED: '1' } as any, {} as any);
    expect(routed.status).toBe(204);
    expect(routed.headers.get('access-control-allow-methods')).toBe('GET, HEAD, OPTIONS');
    const unknown = await worker.fetch(new Request('https://glyphatlas.org/atlas/composer/nope/manifest.json'), {} as any, {} as any);
    expect(unknown.status).toBe(404);
    expect(unknown.headers.get('access-control-allow-origin')).toBe('*');
  });
  it('answers R2 failing as storage unavailable', async () => {
    const broken = { MEDIA: { get: async () => { throw new Error('R2 is down'); } } } as any;
    await expect(composerFile(broken, `${version}/manifest.json`)).rejects.toBeInstanceOf(StorageUnavailable);
  });
});
