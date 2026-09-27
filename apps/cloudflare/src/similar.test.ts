import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { resetPointer, shardOf, similarCrops } from './similar';

// An R2 bucket holding a few objects, read the way the worker reads them.
function bucket(objects: Record<string, Uint8Array | string>) {
  return {
    async get(key: string) {
      const value = objects[key];
      if (value === undefined) return null;
      const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
      return { body: new Response(bytes).body!, json: async () => JSON.parse(new TextDecoder().decode(bytes)) };
    },
  };
}

describe('similar crops', () => {
  beforeEach(() => resetPointer());

  it('reads the crop’s shard and keeps only neighbours the site holds', async () => {
    const shard = await shardOf('ex:1', 3);
    const entries = { 'ex:1': { similar: [['hi:2', 0.91], ['gone', 0.9], ['ex:3', 0.8]], filed_differently: [['ex:3', 0.8]] } };
    const env = { MEDIA: bucket({
      'similar/current.json': JSON.stringify({ revision: 'r1', shard_digits: 3 }),
      [`similar/r1/${shard}.json.gz`]: gzipSync(JSON.stringify(entries)),
    }) } as unknown as Env;
    const held = new Map([['hi:2', { id: 'hi:2', origin: 'corpus' }], ['ex:3', { id: 'ex:3', origin: 'local' }]]);
    const asked: string[][] = [];
    const result = await similarCrops(env, 'ex:1', 12, async (_, ids) => {
      asked.push([...ids]);
      return new Map(ids.filter(i => held.has(i)).map(i => [i, held.get(i)!]));
    });
    // Each id is looked up once, however many lists name it.
    expect(asked).toEqual([['hi:2', 'gone', 'ex:3']]);
    expect(result.revision).toBe('r1');
    expect(result.similar.map((i: any) => [i.id, i.score])).toEqual([['hi:2', 0.91], ['ex:3', 0.8]]);
    expect(result.filed_differently.map((i: any) => i.id)).toEqual(['ex:3']);
    // With a limit of one, only the first id of each list is looked up.
    const limited: string[][] = [];
    const one = await similarCrops(env, 'ex:1', 1, async (_, ids) => {
      limited.push([...ids]);
      return new Map(ids.filter(i => held.has(i)).map(i => [i, held.get(i)!]));
    });
    expect(one.similar.map((i: any) => i.id)).toEqual(['hi:2']);
    expect(limited).toEqual([['hi:2'], ['ex:3']]);
  });

  it('answers empty lists before any revision is published, and for a crop no shard lists', async () => {
    const none = await similarCrops({ MEDIA: bucket({}) } as unknown as Env, 'ex:1', 12, async () => new Map());
    expect(none).toEqual({ revision: null, similar: [], filed_differently: [] });
    resetPointer();
    const env = { MEDIA: bucket({ 'similar/current.json': JSON.stringify({ revision: 'r1', shard_digits: 3 }) }) } as unknown as Env;
    expect(await similarCrops(env, 'ex:9', 12, async () => new Map())).toEqual({ revision: 'r1', similar: [], filed_differently: [] });
  });

  it('shards by the SHA-256 of the id, as the Python side does', async () => {
    // hashlib.sha256(b"hi:34000001").hexdigest()[:3]
    expect(await shardOf('hi:34000001', 3)).toBe(Bun.CryptoHasher.hash('sha256', 'hi:34000001', 'hex').slice(0, 3));
  });
});
