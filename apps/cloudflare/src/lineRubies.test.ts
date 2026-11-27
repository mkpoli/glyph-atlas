import { expect, test } from 'bun:test';
import { gzipSync } from 'node:zlib';
import { lineKey, lineRubies, rubyPack } from './lineRubies';

(globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };

test('a line is keyed by its letters and numbers', () => {
  expect(lineKey('緻密にして、諸器に 製す。')).toBe('緻密にして諸器に製す');
  expect(lineKey('云々〻〇𛁈')).toBe('云々〻〇𛁈');
});

test('a page of a みんなで翻刻 entry names its pack, 32 pages each', () => {
  const entry = 'E542161413FD82DB9919B160FA7DB709';
  expect(rubyPack(`hl:${entry}:27`, 28)).toBe(`v3/rubies/${entry.toLowerCase()}/0.json.gz`);
  expect(rubyPack(`hk:${entry}:32`, 33)).toBe(`v3/rubies/${entry.toLowerCase()}/1.json.gz`);
  for (const [page, number] of [['codh:200014685:3', 4], [`hl:${entry}:27`, 0], [`hl:${entry}:27`, null], [null, 3]]) expect(rubyPack(page, number)).toBeNull();
});

test('a crop\'s line finds its rubies whatever its punctuation, and a line the pack lacks has none', async () => {
  const entry = 'e542161413fd82db9919b160fa7db709', reads: string[] = [];
  const bytes = gzipSync(JSON.stringify({ 28: { '緻密にして諸器に製す': [[5, 2, 0, ['しよ', 'き']]] } }));
  const env = { MEDIA: { get: async (name: string) => { reads.push(name); return name.endsWith(`${entry}/0.json.gz`) ? { body: new Response(bytes).body, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null } } } as unknown as Env;
  const record = { page_id: `hl:${entry}:27`, page_number: 28, text: '緻密にして、諸器に製す' };
  expect((await lineRubies(env, record)).here).toEqual([[5, 2, 0, ['しよ', 'き']]]);
  expect((await lineRubies(env, { ...record, text: '別の行' })).here).toBeNull();
  expect((await lineRubies(env, { ...record, page_number: 29 })).here).toBeNull();
  expect((await lineRubies(env, { ...record, text: '' })).here).toBeNull();
  expect((await lineRubies(env, { page_id: 'codh:x:1', page_number: 2, text: '諸器' })).here).toBeNull();
  expect(reads.every(name => name.startsWith('v3/rubies/'))).toBe(true);
});

test('a word under no ruby on its line is given the readings the same pages give it elsewhere', async () => {
  const entry = '0123456789abcdef0123456789abcdef';
  const pack = {
    28: { '緻密にして諸器に製す': [[5, 2, 0, ['しよ', 'き']], [0, 2, 1, 'こまか']], '諸器を見る': [[0, 2, 0, 'しよき']] },
    29: { '器と諸器': [[0, 1, 0, 'うつは'], [2, 2, 0, 'しよき']], '器': [[0, 1, 0, 'き']] },
  };
  const bytes = gzipSync(JSON.stringify(pack));
  const env = { MEDIA: { get: async (name: string) => name.endsWith(`${entry}/0.json.gz`) ? { body: new Response(bytes).body, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null } } as unknown as Env;
  const page = { page_id: `hl:${entry}:29`, page_number: 30 };
  // 諸器 stands on three lines as しよき, once cut over its characters; 器 alone is read two ways, a line each.
  expect((await lineRubies(env, { ...page, text: 'この諸器、' })).elsewhere).toEqual([[2, 2, ['しよ', 'き'], 3, 28], [3, 1, 'うつは', 1, 29], [3, 1, 'き', 1, 29]]);
  // The line's own ruby stands over 諸器, so nothing is offered under it; a 左訓 keeps no word from one.
  expect((await lineRubies(env, { page_id: `hl:${entry}:27`, page_number: 28, text: '緻密にして諸器に製す' })).elsewhere).toEqual([]);
  expect((await lineRubies(env, { ...page, text: '別の行' })).elsewhere).toEqual([]);
  // A word the punctuation parts is none on this line; its characters alone still are.
  expect((await lineRubies(env, { ...page, text: '諸、器' })).elsewhere).toEqual([[1, 1, 'うつは', 1, 29], [1, 1, 'き', 1, 29]]);
});
