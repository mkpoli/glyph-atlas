import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { LIST_PAGE, cardList, characterObject, characterStructure, familySummaries, referenceShard } from './atlas3Reference';

const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());

function bucket(objects: Record<string, unknown>) {
  const reads: string[] = [];
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    const value = objects[name];
    return value === undefined ? null : { arrayBuffer: async () => new Uint8Array(gzipSync(JSON.stringify(value))).buffer };
  } };
  return { env: { MEDIA } as unknown as Env, reads };
}

describe('reference objects', () => {
  it('name a shard by the first two hex digits of the SHA-256, as reference.py does', async () => {
    // Python: hashlib.sha256(key.encode()).hexdigest()[:2]
    expect(await referenceShard('U+3042')).toBe('08');
    expect(await referenceShard('好')).toBe('f8');
  });

  it('read a card by its code points and answer null for one no card has', async () => {
    const { env } = bucket({ 'v3/reference/char/U+3042-U+3099.json.gz': { grapheme: 9 } });
    expect((await characterObject(env, 'U+3042 U+3099'))?.grapheme).toBe(9);
    expect(await characterObject(env, 'U+0000')).toBeNull();
  });

  it('read each family shard once for members of several heads', async () => {
    const a = await referenceShard('U+3044'), b = await referenceShard('U+3046');
    const { env, reads } = bucket({ [`v3/reference/summary/${a}.json.gz`]: { 'U+3044': { char: 'い' }, 'U+1B006': { char: '𛀆' } },
      [`v3/reference/summary/${b}.json.gz`]: { 'U+3046': { char: 'う' } } });
    const found = await familySummaries(env, ['U+3044', 'U+3044', 'U+3046']);
    expect([...found.keys()].sort()).toEqual(['U+1B006', 'U+3044', 'U+3046']);
    expect(reads.length).toBe(a === b ? 1 : 2);
  });

  it('find a structure and answer null for a character the shard lacks', async () => {
    const { env } = bucket({ [`v3/reference/ids/${await referenceShard('姥')}.json.gz`]: { '姥': [['⿰女老'], null] } });
    expect(await characterStructure(env, '姥')).toEqual({ sequences: ['⿰女老'], shapes: null });
    expect(await characterStructure(env, 'constructor')).toBeNull();
  });

  it('page a list across two objects at most', async () => {
    const items = Array.from({ length: LIST_PAGE + 10 }, (_, i) => ({ i }));
    const { env, reads } = bucket({
      'v3/reference/list/graphemes/0.json.gz': { total: items.length, offset: 0, items: items.slice(0, LIST_PAGE) },
      'v3/reference/list/graphemes/1.json.gz': { total: items.length, offset: LIST_PAGE, items: items.slice(LIST_PAGE) } });
    const page = await cardList(env, 'graphemes', LIST_PAGE - 2, 5);
    expect(page).toEqual({ items: items.slice(LIST_PAGE - 2, LIST_PAGE + 3), total: LIST_PAGE + 10 });
    expect(reads.length).toBe(2);
  });
});
