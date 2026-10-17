import { describe, it, expect } from 'bun:test';
import { readFileSync } from 'node:fs';

const table: Record<string, string[]> = JSON.parse(readFileSync(new URL('../../review/static/refs/kanji-readings.json', import.meta.url), 'utf8')).readings;

describe('kanji found by a Japanese reading', () => {
  it('keys 音 and 訓 alike in hiragana', () => {
    expect(table['とき']).toContain('時');
    expect(table['じ']).toContain('時');
    expect(table['ジ']).toBeUndefined();
  });
  it('lists the kanji the corpus attests most within the first page', () => {
    for (const [reading, char] of [['とき', '時'], ['こう', '高'], ['こう', '行'], ['しょう', '生']]) {
      expect(table[reading].indexOf(char)).toBeGreaterThanOrEqual(0);
      expect(table[reading].indexOf(char)).toBeLessThan(8);
    }
  });
});
