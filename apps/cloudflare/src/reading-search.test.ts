import { describe, it, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { rankReadingKanji } from './index';

const table: Record<string, string[]> = JSON.parse(readFileSync(new URL('../../review/static/refs/kanji-readings.json', import.meta.url), 'utf8')).readings;

describe('kanji found by a Japanese reading', () => {
  it('keys 音 and 訓 alike in hiragana', () => {
    expect(table['とき']).toContain('時');
    expect(table['じ']).toContain('時');
    expect(table['ジ']).toBeUndefined();
  });
  it('lists 常用漢字 before the rest', () => {
    const list = table['とき'];
    expect(list.indexOf('時')).toBeLessThan(list.indexOf('鴇'));
    expect(list.indexOf('時')).toBeLessThan(list.indexOf('旹'));
  });
  it('shows the most attested first and keeps the table order between equals', () => {
    const window = ['斎', '時', '刻', '鴇'];
    const cards = [{ char: '斎', occurrence_count: 3 }, { char: '時', occurrence_count: 900 }, { char: '刻', occurrence_count: 3 }, { char: '鴇' }];
    expect(rankReadingKanji(window, cards).map(card => card.char)).toEqual(['時', '斎', '刻', '鴇']);
  });
});
