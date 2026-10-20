import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from './forms.test';
import { bundleKey, cropId, publicId } from './atlas3';
import { OLD_URL_STATUS, STALE_CODE, StaleCrop, cropParam, cropUrl, movedResponse, staleResponse, writtenCropField } from './atlas3Crops';

// The Workers cache, as far as alias reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

let pages = 5000;
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  db.exec(readFileSync(new URL('../migrations-atlas/0001_init.sql', import.meta.url), 'utf8'));
  // Each test takes pages and text ids of its own, since bundles and alias shards are held per isolate.
  const page = ++pages, n = page;
  const live = cropId(page, 0), retired = cropId(page, 1), orphan = cropId(page, 2), chained = cropId(page, 3), byNumber = cropId(page, 4);
  db.exec(`INSERT INTO books VALUES(1,'hk:book');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'hk:book:${n}',1,2,8);
    INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(${live},0,1,0,1,1);`);
  const text = { live: `hk:${n}:live`, retired: `hk:${n}:retired`, orphan: `hk:${n}:orphan`, chained: `hk:${n}:chained`,
    redirected: `hk:${n}:redirected`, gone: `hk:${n}:gone`, ends: `hk:${n}:ends`, wide: `tsj:s${n}a10７:0` };
  // The alias map: each mirror id to its crop, and a redirect source to the crop its chain ends at,
  // which for a retired crop is the retired crop itself.
  const aliases: Record<string, number> = { [text.live]: live, [text.retired]: retired, [text.orphan]: orphan,
    [text.chained]: chained, [text.redirected]: retired, [text.ends]: cropId(page, 7), [text.wide]: live };
  const shards = new Map<string, Record<string, number>>();
  for (const [t, id] of Object.entries(aliases)) {
    const name = `v3/aliases/${createHash('sha256').update(t).digest('hex').slice(0, 3)}.json.gz`;
    shards.set(name, { ...shards.get(name), [t]: id });
  }
  const objects = new Map<string, Uint8Array>([...shards].map(([name, shard]) => [name, gzipSync(JSON.stringify(shard))]));
  objects.set(bundleKey(page, 2), gzipSync(JSON.stringify({ key: `hk:book:${n}`, book: 1, rev: 2, page: null, crops: [
    { slot: 0, id: text.live },
    { slot: 1, id: text.retired, retired: true, replaced_by: text.live },
    { slot: 2, id: text.orphan, retired: true },
    { slot: 3, id: text.chained, retired: true, replaced_by: text.retired },
    { slot: 4, id: `hk:${n}:number`, retired: true, replaced_by: live },
    { slot: 5, id: `hk:${n}:loop`, retired: true, replaced_by: cropId(page, 6) },
    { slot: 6, id: `hk:${n}:loop2`, retired: true, replaced_by: cropId(page, 5) },
    { slot: 7, id: text.ends, retired: true, replaced_by: text.orphan }] })));
  const MEDIA = { get: async (name: string) => {
    const bytes = objects.get(name);
    return bytes ? { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null;
  } };
  const env = { ATLAS: d1(db), MEDIA } as unknown as Env;
  return { db, env, text, live, retired, orphan, chained, byNumber };
}

describe('a crop named in a route', () => {
  it('is its public id, read as written', async () => {
    const { env, live } = setup();
    expect(await cropParam(env, publicId(live))).toEqual({ id: live });
  });

  it('under another spelling of its public id moves to the one spelling', async () => {
    const { env, live } = setup();
    expect(await cropParam(env, publicId(live).toUpperCase())).toEqual({ moved: live });
  });

  it('by its old text id moves to its public id', async () => {
    const { env, text, live } = setup();
    expect(await cropParam(env, text.live)).toEqual({ moved: live });
  });

  it('retired in favour of another moves to that one, by text id or by public id', async () => {
    const { env, text, live, retired, chained, byNumber } = setup();
    expect(await cropParam(env, text.retired)).toEqual({ moved: live });
    expect(await cropParam(env, publicId(retired))).toEqual({ moved: live });
    expect(await cropParam(env, text.chained)).toEqual({ moved: live });
    expect(await cropParam(env, publicId(chained))).toEqual({ moved: live });
    expect(await cropParam(env, publicId(byNumber))).toEqual({ moved: live });
  });

  it('through a redirect source whose alias is its retired crop, moves to the replacement', async () => {
    const { env, text, live } = setup();
    expect(await cropParam(env, text.redirected)).toEqual({ moved: live });
  });

  it('retired with no replacement is told apart from no crop', async () => {
    const { env, text, orphan } = setup();
    expect(await cropParam(env, publicId(orphan))).toEqual({ retired: orphan, canonical: true });
    expect(await cropParam(env, text.orphan)).toEqual({ retired: orphan, canonical: false });
    expect(await cropParam(env, publicId(orphan).toUpperCase())).toEqual({ retired: orphan, canonical: false });
    expect(await cropParam(env, text.gone)).toBeNull();
    expect(await cropParam(env, publicId(cropId(pages + 1000, 0)))).toBeNull();
    expect(await cropParam(env, 'not an id')).toBeNull();
  });

  it('retired in favour of a crop retired with no replacement is that crop, to be moved to', async () => {
    const { env, text, orphan } = setup();
    expect(await cropParam(env, text.ends)).toEqual({ retired: orphan, canonical: false });
    expect(await cropParam(env, publicId(cropId(pages, 7)))).toEqual({ retired: orphan, canonical: false });
  });

  it('is percent-decoded once, as written in the path', async () => {
    const { env, text, live } = setup();
    const raw = new URL(`https://glyphatlas.org/atlas/characters/${text.wide}`).pathname.split('/').at(-1)!;
    expect(raw).toContain('%EF%BC%97');
    expect(await cropParam(env, raw)).toEqual({ moved: live });
    expect(await cropParam(env, text.wide)).toEqual({ moved: live });
    expect(await cropParam(env, encodeURIComponent(encodeURIComponent(text.wide)))).toBeNull();
    expect(await cropParam(env, '%E0%A4%A')).toBeNull();
  });

  it('ends with nothing at a loop of replacements or a replacement gone', async () => {
    const { db, env, text, live } = setup();
    expect(await cropParam(env, publicId(cropId(pages, 5)))).toBeNull();
    db.exec(`DELETE FROM crops WHERE id=${live}`);
    expect(await cropParam(env, text.retired)).toBeNull();
  });
});

describe('an old crop URL', () => {
  it('is answered with the public id in its place, in the path or the query', () => {
    const id = cropId(7, 3);
    const url = new URL('https://glyphatlas.org/atlas/characters/hk%3Aa%3A1/versions?around=hk:a:1&n=2');
    expect(cropUrl(url, 'hk%3Aa%3A1', id).href).toBe(`https://glyphatlas.org/atlas/characters/${publicId(id)}/versions?around=${publicId(id)}&n=2`);
    const response = movedResponse(new URL('https://glyphatlas.org/atlas/characters/ex:1:日本'), 'ex:1:%E6%97%A5%E6%9C%AC', id);
    expect(response.status).toBe(OLD_URL_STATUS);
    expect(OLD_URL_STATUS).toBe(302);
    expect(response.headers.get('location')).toBe(`https://glyphatlas.org/atlas/characters/${publicId(id)}`);
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('that holds no segment to replace is answered 404, never a redirect to itself', async () => {
    const id = cropId(7, 3);
    const response = movedResponse(new URL('https://glyphatlas.org/atlas/characters?crop=hk:a:2'), 'hk%3Aa%3A1', id);
    expect(response.status).toBe(404);
    expect(response.headers.get('location')).toBeNull();
  });
});

describe('a crop field of a write', () => {
  it('is its public id', async () => {
    const { env } = setup();
    const id = cropId(11932, 255);
    expect(await writtenCropField(env, publicId(id))).toBe(id);
    expect(await writtenCropField(env, 7)).toBeNull();
    expect(await writtenCropField(env, '0')).toBeNull();
  });

  it('by an old text id, as a save held through the freeze sends it, is the crop it named', async () => {
    const { env, text, live, retired } = setup();
    expect(await writtenCropField(env, text.live)).toBe(live);
    expect(await writtenCropField(env, text.retired)).toBe(retired);
  });

  it('by a text id the alias map does not hold is refused with a prompt to reload', async () => {
    const { env, text } = setup();
    await expect(writtenCropField(env, text.gone)).rejects.toThrow(StaleCrop);
    const response = staleResponse();
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ code: STALE_CODE });
  });
});
