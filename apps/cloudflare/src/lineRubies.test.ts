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
  expect(await lineRubies(env, record)).toEqual([[5, 2, 0, ['しよ', 'き']]]);
  expect(await lineRubies(env, { ...record, text: '別の行' })).toBeNull();
  expect(await lineRubies(env, { ...record, page_number: 29 })).toBeNull();
  expect(await lineRubies(env, { ...record, text: '' })).toBeNull();
  expect(await lineRubies(env, { page_id: 'codh:x:1', page_number: 2, text: '諸器' })).toBeNull();
  expect(reads.every(name => name.startsWith('v3/rubies/'))).toBe(true);
});
