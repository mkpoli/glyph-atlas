import { beforeEach, describe, expect, it } from 'bun:test';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { aliasCrop, aliasShardKey } from './atlas3Aliases';

// The Workers cache, as far as these reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());

let n = 0;
function setup() {
  // Each test takes its own text ids, since shards are held per isolate.
  const ids = { [`hk:${++n}:a`]: 385875968 + n, [`ex:${n}:日本`]: 7 };
  const shards = new Map<string, Record<string, number>>();
  for (const [text, id] of Object.entries(ids)) {
    const name = `v3/aliases/${createHash('sha256').update(text).digest('hex').slice(0, 3)}.json.gz`;
    shards.set(name, { ...shards.get(name), [text]: id });
  }
  const objects = new Map([...shards].map(([name, shard]) => [name, gzipSync(JSON.stringify(shard))]));
  const reads: string[] = [];
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    const bytes = objects.get(name);
    return bytes ? { arrayBuffer: async () => bytes.slice().buffer } : null;
  } };
  return { env: { MEDIA } as unknown as Env, ids, reads };
}

describe('old crop URLs', () => {
  it('name a shard by the first three hex digits of the SHA-256, as aliases.py does', async () => {
    // Python: hashlib.sha256(text.encode()).hexdigest()[:3]
    expect(await aliasShardKey('ex:a1')).toBe('v3/aliases/b84.json.gz');
    expect(await aliasShardKey('日本')).toBe('v3/aliases/cf2.json.gz');
  });

  it('find a text id’s crop and answer null for one the map lacks', async () => {
    const { env, ids } = setup();
    for (const [text, id] of Object.entries(ids)) expect(await aliasCrop(env, text)).toBe(id);
    expect(await aliasCrop(env, 'hk:never')).toBeNull();
    expect(await aliasCrop(env, '')).toBeNull();
    expect(await aliasCrop(env, 'constructor')).toBeNull();
  });

  it('read a shard from R2 once, then from the isolate and the edge', async () => {
    const { env, ids, reads } = setup();
    const text = Object.keys(ids)[0], name = await aliasShardKey(text);
    await aliasCrop(env, text);
    await aliasCrop(env, text);
    expect(reads.filter(r => r === name)).toHaveLength(1);
    expect(edge.has(`https://glyphatlas.org/_shards/${name}`)).toBe(true);
    edge.clear();
    await aliasCrop(env, text, undefined, Date.now() + 301_000);
    expect(reads.filter(r => r === name)).toHaveLength(2);
  });
});
