import { describe, expect, it } from 'bun:test';
import { StorageUnavailable } from './busy';
import { counted } from './rateLimit';
import { typefaceFile } from './typefaces';
import { TYPEFACE_VERSION } from './typefaceVersions';

const version = TYPEFACE_VERSION;
const stored = new Map([[`fonts/${version}/sans/04e.woff2`, new Uint8Array([0x77, 0x4f, 0x46, 0x32])],
  [`fonts/${version}/serif/figures/200.woff2`, new Uint8Array([0x77, 0x4f])], [`fonts/${version}/serif/c0.woff2`, new Uint8Array([0x77])],
  [`fonts/${version}/index.json`, new TextEncoder().encode('{}')], [`fonts/${version}/serif.css`, new TextEncoder().encode('@font-face{}')],
  [`fonts/${version}/sans-figures.css`, new TextEncoder().encode('@font-face{}')], [`fonts/${version}/OFL.txt`, new TextEncoder().encode('SIL')]]);
const env = { MEDIA: { async get(key: string) {
  const bytes = stored.get(key);
  return bytes ? { body: new Response(bytes).body!, size: bytes.length, httpEtag: '"e"' } : null;
} } } as any;

describe('typefaceFile', () => {
  it('serves a stored file of a version for good', async () => {
    const response = (await typefaceFile(env, `${version}/sans/04e.woff2`))!;
    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([0x77, 0x4f, 0x46, 0x32]));
    expect(response.headers.get('content-type')).toBe('font/woff2');
    expect(response.headers.get('cache-control')).toBe('public, max-age=31536000, immutable');
    expect((await typefaceFile(env, `${version}/serif/figures/200.woff2`))!.status).toBe(200);
    expect((await typefaceFile(env, `${version}/serif/c0.woff2`))!.status).toBe(200);
    expect((await typefaceFile(env, `${version}/index.json`))!.headers.get('content-type')).toBe('application/json');
    expect((await typefaceFile(env, `${version}/serif.css`))!.headers.get('content-type')).toBe('text/css; charset=utf-8');
    expect((await typefaceFile(env, `${version}/sans-figures.css`))!.status).toBe(200);
    expect((await typefaceFile(env, `${version}/OFL.txt`))!.headers.get('content-type')).toBe('text/plain; charset=utf-8');
  });
  it('answers a file the version lacks as missing, and nothing for a path naming no file or another version', async () => {
    const missing = (await typefaceFile(env, `${version}/sans/04f.woff2`))!;
    expect(missing.status).toBe(404);
    expect(missing.headers.get('cache-control')).toBe('public, max-age=300');
    for (const bad of [`${version}/../x.woff2`, `${version}/kaisho/04e.woff2`, `${version}/sans/04e.ttf`, `${version}/sans/4E.woff2`,
      `${version}/sans/figures/c0.woff2`, `${version}/kaisho.css`, `${version}/sans/x.css`, 'sans/04e.woff2', `0123456789ab/sans/04e.woff2`])
      expect(await typefaceFile(env, bad)).toBeNull();
  });
  it('keeps an answer at the edge', async () => {
    const kept = new Map<string, Response>(), waited: Promise<unknown>[] = [];
    (globalThis as any).caches = { default: { match: async (r: Request) => kept.get(r.url)?.clone(), put: async (r: Request, res: Response) => { kept.set(r.url, res); } } };
    try {
      const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as any;
      const url = `https://glyphatlas.org/atlas/fonts/${version}/sans/04e.woff2`;
      expect((await typefaceFile(env, `${version}/sans/04e.woff2`, new Request(url), ctx))!.status).toBe(200);
      await Promise.all(waited);
      // Asked again, the edge answers; R2 is not read.
      const unread = { MEDIA: { get: async () => { throw new Error('R2 was read'); } } } as any;
      expect((await typefaceFile(unread, `${version}/sans/04e.woff2`, new Request(url), ctx))!.status).toBe(200);
    } finally { delete (globalThis as any).caches; }
  });
  it('answers R2 failing as storage unavailable', async () => {
    const broken = { MEDIA: { get: async () => { throw new Error('R2 is down'); } } } as any;
    await expect(typefaceFile(broken, `${version}/index.json`)).rejects.toBeInstanceOf(StorageUnavailable);
  });
  it('costs its reader none of the request allowance', () => {
    expect(counted(`/atlas/fonts/${version}/sans/04e.woff2`)).toBe(false);
  });
});
