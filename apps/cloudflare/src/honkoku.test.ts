import { describe, expect, test } from 'bun:test';
import { honkokuPage } from './honkoku';

const ENTRY = '68C7BEFABCCE5DD6A2327799118C7CD1';
const url = (page: number, entry = ENTRY) => `https://app.honkoku.org/transcription/${entry}/${page}`;

describe('honkokuPage', () => {
  test('a Honkoku-Lines corpus crop counts its 0-based page id from 1', () => {
    const item = { source: { corpus: 'honkoku-lines', document_id: `hl:${ENTRY}`, page_id: `hl:${ENTRY}:22` } };
    expect(honkokuPage(item, `hl:${ENTRY}`)).toBe(url(23));
  });
  test('an Ainu records corpus crop counts its 0-based page id from 1', () => {
    const entry = '0916dafb80cdc48ca7687afcad4a4f35';
    const item = { source: { corpus: 'ainu-records', document_id: `hk:${entry}`, page_id: `hk:${entry}:5` } };
    expect(honkokuPage(item, `hk:${entry}`)).toBe(url(6, entry));
  });
  test('a local crop uses its page number', () => {
    const entry = '0916dafb80cdc48ca7687afcad4a4f35';
    expect(honkokuPage({ id: 'ar:ezo-kiko--ryukoku-1:2-ocr10-0', page_number: 2 }, `hk:${entry}`)).toBe(url(2, entry));
  });
  test('the first page is 1', () => {
    const item = { source: { corpus: 'honkoku-lines', page_id: `hl:${ENTRY}:0` } };
    expect(honkokuPage(item, `hl:${ENTRY}`)).toBe(url(1));
  });
  test('an hk: corpus numbered from 1 is not guessed', () => {
    const item = { source: { corpus: 'honkoku-data', page_id: `hk:${ENTRY}:5` } };
    expect(honkokuPage(item, `hk:${ENTRY}`)).toBeNull();
  });
  test('other sources and documents without an entry id get no link', () => {
    expect(honkokuPage({ page_number: 3 }, 'codh:200003803')).toBeNull();
    expect(honkokuPage({ page_number: 3 }, 'ws:abc')).toBeNull();
    expect(honkokuPage({ page_number: 3 }, `hl:${ENTRY.slice(1)}`)).toBeNull();
    expect(honkokuPage({ page_number: 3 }, null)).toBeNull();
    expect(honkokuPage({ page_number: 0 }, `hl:${ENTRY}`)).toBeNull();
  });
});
