import { beforeEach, describe, expect, it } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { cropVotes, restoreVotes, votesKey } from './atlas3Votes';

// The Workers cache, as far as these reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());

const ENGINES = [{ name: 'NDLkotenOCR', sha256: 'fd75' }, { name: 'Atlas classifier', sha256: '4360' }];
const STORED = { cut: 1, models: 3,
  visual: { candidates: [{ text: '三', score: 0.77, engine: 0 }], votes: [{ text: '三', score: 0.88, engine: 1, members: ['三'] }] },
  context: { line_id: 'hk:1:L13', anchor: 9, source_text: '三', candidates: [['三', 0, 'モ載タリ', '十日ノ旅']] } };

let page = 100;
function setup() {
  // Each test takes its own page, since objects are held per isolate.
  const p = ++page, objects = new Map([[votesKey(p), gzipSync(JSON.stringify({ 5: STORED, 6: { cut: 0, models: null, context: STORED.context } }))]]);
  const reads: string[] = [];
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    const bytes = objects.get(name);
    return bytes ? { arrayBuffer: async () => bytes.slice().buffer } : null;
  } };
  return { env: { MEDIA } as unknown as Env, id: (slot: number) => p * 2 ** 24 + slot, reads };
}

describe('model votes', () => {
  it('answer a crop’s readings only for the cut they were made on', async () => {
    const { env, id, reads } = setup();
    expect(await cropVotes(env, id(5), 1)).toEqual(STORED);
    expect(await cropVotes(env, id(5), 2)).toBeNull();
    expect(await cropVotes(env, id(7), 0)).toBeNull();
    expect(reads).toHaveLength(1);
  });

  it('answer null for a page with no object', async () => {
    const { env } = setup();
    expect(await cropVotes(env, 99_999 * 2 ** 24, 0)).toBeNull();
  });

  it('restore the routes’ answer with the engines named', async () => {
    const back = restoreVotes(ENGINES, STORED);
    expect(back.visual).toEqual({ status: 'ready', engines: ENGINES, verified: false,
      candidates: [{ text: '三', score: 0.77, engine: 'NDLkotenOCR' }],
      votes: [{ text: '三', score: 0.88, engine: 'Atlas classifier', members: ['三'] }] });
    expect(back.context).toEqual({ status: 'ready', basis: 'transcription', line_id: 'hk:1:L13', anchor: 9, source_text: '三',
      candidates: [{ text: '三', engine: 'Nearby transcription', offset: 0, before: 'モ載タリ', after: '十日ノ旅' }] });
    expect(restoreVotes(null, null)).toEqual({ visual: { status: 'unavailable', candidates: [] },
      context: { status: 'unavailable', candidates: [], basis: 'transcription' } });
  });
});
