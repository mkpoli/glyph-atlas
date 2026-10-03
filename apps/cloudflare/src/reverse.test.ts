import { beforeEach, describe, expect, it } from 'bun:test';
import { CANDIDATE_LIMIT, QueryError, addressKey, boundedText, decodeCandidates, decodeVector, modelFile, modelInfo, query, resetPointers } from './reverse';

const encoder = 'a'.repeat(64);
const model = { version: '0123456789abcdef', encoder, features: 4, preprocessing: { size: 128 },
  files: { model: { name: 'classifier.onnx', bytes: 10, sha256: 'm' }, classes: { name: 'classes.json', bytes: 2, sha256: 'c' } } };
const pointer = { index: 'glyph-atlas-similar-aaaaaaaa', encoder, revision: 'r1', crops: 3, dimensions: 4 };

function bucket(objects: Record<string, string>) {
  return {
    async get(key: string) {
      const value = objects[key];
      if (value === undefined) return null;
      const bytes = new TextEncoder().encode(value);
      return { body: new Response(bytes).body!, size: bytes.length, httpEtag: '"e"', json: async () => JSON.parse(value) };
    },
    async head(key: string) { return objects[key] === undefined ? null : { size: objects[key].length, httpEtag: '"e"' } },
  };
}

function encode(values: number[]) {
  const bytes = new Uint8Array(new Float32Array(values).buffer);
  return btoa(String.fromCharCode(...bytes));
}

function environment(matches: Record<string, [string, number][]>, asked: any[] = []) {
  return {
    SIMILAR_INDEX_NAME: pointer.index,
    MEDIA: bucket({ 'reverse/model.json': JSON.stringify(model), [`reverse/index/${pointer.index}.json`]: JSON.stringify(pointer),
      'reverse/models/0123456789abcdef/classifier.onnx': 'onnx bytes' }),
    SIMILAR_INDEX: {
      async query(vector: number[], options: any) {
        asked.push({ vector, options });
        const key = options.filter ? JSON.stringify(options.filter.label) : 'all';
        return { count: 0, matches: (matches[key] ?? []).map(([id, score]) => ({ id, score })) };
      },
    },
  } as unknown as Env;
}

const held = async (_: Env, ids: string[]) => new Map(ids.filter(i => !i.startsWith('gone')).map(i => [i, { id: i, label: i.slice(-1) }]));

describe('image search', () => {
  beforeEach(() => resetPointers());

  it('describes the model and says the index answers its vectors', async () => {
    const info = await modelInfo(environment({}));
    expect(info.ready).toBe(true);
    expect(info.model).toMatchObject({ version: '0123456789abcdef', files: { model: { path: 'models/0123456789abcdef/classifier.onnx', bytes: 10, sha256: 'm' } } });
    expect(info.index).toEqual({ revision: 'r1', crops: 3, encoder });
  });

  it('serves model files immutably and nothing outside them', async () => {
    const env = environment({});
    const file = await modelFile(env, 'models/0123456789abcdef/classifier.onnx');
    expect(file!.headers.get('cache-control')).toContain('immutable');
    expect(await file!.text()).toBe('onnx bytes');
    expect(await modelFile(env, 'model.json')).toBeNull();
    expect(await modelFile(env, '../similar/current.json')).toBeNull();
    expect(await modelFile(env, 'models/0123456789abcdef/other.bin')).toBeNull();
  });

  it('searches every crop and each candidate’s crops, and leaves out crops the site no longer holds', async () => {
    const asked: any[] = [];
    const env = environment({ all: [['x:字', 0.91], ['gone:1', 0.9], ['y:宇', 0.8]],
      '"字"': [['x:字', 0.91]], '{"$in":["国","國"]}': [['z:國', 0.7]] }, asked);
    const vector = [0.5, 0.5, 0.5, 0.5];
    const result = await query(env, { encoder, vector: encode(vector), candidates: [['字'], ['国', '國', '国']] }, held);
    expect(result.similar.map((c: any) => [c.id, c.score])).toEqual([['x:字', 0.91], ['y:宇', 0.8]]);
    expect(result.candidates.map((c: any) => [c.characters, c.crops.map((i: any) => i.id)]))
      .toEqual([[['字'], ['x:字']], [['国', '國'], ['z:國']]]);
    expect(asked[1].options).toMatchObject({ topK: CANDIDATE_LIMIT * 3, returnMetadata: 'none', filter: { label: '字' } });
    expect(asked[0].vector).toEqual(vector);
  });

  it('leaves out a candidate’s crop relabelled since the index was filled', async () => {
    const env = environment({ '"字"': [['x:宇', 0.9], ['y:字', 0.8]] });
    const result = await query(env, { encoder, vector: encode([1, 0, 0, 0]), candidates: [['字']] }, held);
    expect(result.candidates[0].crops.map((c: any) => c.id)).toEqual(['y:字']);
  });

  it('reads the pointer of the index it is bound to, and answers 503 when Vectorize fails', async () => {
    const other = { ...environment({}), SIMILAR_INDEX_NAME: 'glyph-atlas-similar-bbbbbbbb' } as unknown as Env;
    expect((await modelInfo(other)).ready).toBe(false);
    resetPointers();
    const failing = { ...environment({}), SIMILAR_INDEX: { query: async () => { throw new Error('down') } } } as unknown as Env;
    expect((await query(failing, { encoder, vector: encode([1, 0, 0, 0]) }, held).catch(e => e)).status).toBe(503);
    resetPointers();
    const broken = { ...environment({}), MEDIA: bucket({ 'reverse/model.json': '{"version":1}' }) } as unknown as Env;
    expect(await modelInfo(broken)).toEqual({ model: null, index: null, ready: false });
  });

  it('keys an IPv6 address by its /64 and reads no more body than allowed', async () => {
    expect(addressKey('2001:db8:0:1:aaaa:bbbb:cccc:dddd')).toBe('2001:db8:0:1::/64');
    expect(addressKey('2001:db8::1')).toBe('2001:db8:0:0::/64');
    expect(addressKey('192.0.2.1')).toBe('192.0.2.1');
    const big = new Request('https://x/', { method: 'POST', body: new ReadableStream({ start(c) { for (let i = 0; i < 4; i++) c.enqueue(new Uint8Array(8000)); c.close() } }) });
    expect(await boundedText(big, 16384)).toBeNull();
    expect(await boundedText(new Request('https://x/', { method: 'POST', body: '{"a":1}' }), 16384)).toBe('{"a":1}');
  });

  it('refuses another model’s vectors with the version to update to', async () => {
    const env = environment({});
    const error = await query(env, { encoder: 'b'.repeat(64), vector: encode([1, 0, 0, 0]) }, held).catch(e => e);
    expect(error).toBeInstanceOf(QueryError);
    expect([error.status, error.extra]).toEqual([409, { error: 'model', version: '0123456789abcdef' }]);
  });

  it('accepts only a unit vector of the index’s width', () => {
    expect(decodeVector(encode([1, 0, 0, 0]), 4)).toEqual([1, 0, 0, 0]);
    for (const bad of [encode([1, 0, 0]), encode([2, 0, 0, 0]), encode([NaN, 0, 0, 0]), 'not base64!!!!!!!!!!!', 42, undefined])
      expect(() => decodeVector(bad, 4)).toThrow(QueryError);
  });

  it('accepts at most five candidates of short character lists', () => {
    expect(decodeCandidates(undefined)).toEqual([]);
    expect(decodeCandidates([['か']])).toEqual([['か']]);
    for (const bad of [[[]], [['x'.repeat(9)]], [[1]], 'か', Array(6).fill(['か']), [Array(17).fill('か')], [['\uD800']], [['a\u0000']]])
      expect(() => decodeCandidates(bad)).toThrow(QueryError);
  });

  it('answers 503 before an index is published', async () => {
    const env = { MEDIA: bucket({}), SIMILAR_INDEX: {}, SIMILAR_INDEX_NAME: pointer.index } as unknown as Env;
    expect((await query(env, {}, held).catch(e => e)).status).toBe(503);
    resetPointers();
    expect((await modelInfo(env)).ready).toBe(false);
  });
});

describe('the image search route', () => {
  const post = (body: string) => new Request('https://glyphatlas.org/atlas/similar/query', { method: 'POST', body,
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '192.0.2.1' } });
  const routed = async (env: Env, body: string) => (await import('./index')).default.fetch(post(body), env, {} as ExecutionContext);

  it('holds an address to a rate before reading the body, and needs no session', async () => {
    const keys: string[] = [];
    const env = { ...environment({}), REVERSE_QUERIES: { limit: async ({ key }: { key: string }) => { keys.push(key); return { success: false } } } } as unknown as Env;
    const response = await routed(env, '{}');
    expect(response.status).toBe(429);
    expect(keys).toEqual(['192.0.2.1']);
  });

  it('refuses a body larger than a vector needs, and one that is not an object', async () => {
    const env = { ...environment({}), REVERSE_QUERIES: { limit: async () => ({ success: true }) } } as unknown as Env;
    expect((await routed(env, JSON.stringify({ vector: 'x'.repeat(20000) }))).status).toBe(413);
    expect((await routed(env, '[1]')).status).toBe(400);
    expect((await routed(env, 'not json')).status).toBe(400);
    resetPointers();
    const answered = await routed(env, JSON.stringify({ encoder, vector: encode([1, 0, 0, 0]) }));
    expect(answered.status).toBe(200);
    expect(answered.headers.get('cache-control')).toBe('no-store');
  });
});
