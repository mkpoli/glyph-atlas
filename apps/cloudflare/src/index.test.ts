import { describe, it, expect } from 'bun:test';
import { canonical, literal, hira, single, readingFrom, validRound } from './index';

describe('historical character identities', () => {
  it('keeps supplementary characters intact', () => {
    expect(literal('U+2A708')).toBe('𪜈');
    expect(single('𪜈')).toBe(true);
    expect(single('トモ')).toBe(false);
  });
  it('normalizes combining kana without rewriting written forms', () => {
    expect(literal('か\u3099')).toBe('が');
    expect(hira('トモ')).toBe('とも');
    expect(literal('假')).toBe('假');
    expect(literal('仮')).toBe('仮');
  });
  it('canonicalizes idempotency payloads independently of object key order', () => {
    expect(canonical({ id:'one', answers:[{ issue:'merged', correction:'トモ' }] }))
      .toBe(canonical({ answers:[{ correction:'トモ', issue:'merged' }], id:'one' }));
    expect(canonical({ character:'ム' })).not.toBe(canonical({ character:'厶' }));
  });
});

it('a compatibility ideograph stays the character it is, and a voiced kana still composes', () => {
  expect(literal('U+FA30')).toBe('侮')
  expect(literal('侮')).toBe('侮')
  expect(literal('が')).toBe('が')
})

it('a corrected character carries the reading the character layer states', () => {
  expect(readingFrom({ char: 'り', script: 'hiragana', readings: ['り'] })).toBe('り')
  expect(readingFrom({ char: '𪜈', script: 'han', readings: [], ligature: { reading: 'トモ' } })).toBe('とも')
  expect(readingFrom({ char: '国', script: 'han', readings: [] })).toBe('国')
  expect(readingFrom({ char: '𛄝', script: 'hentaigana', readings: ['ん', 'む', 'も'] })).toBe(null)
})

describe('a round names flagged answers, seen crops, or both', () => {
  const hash = 'a'.repeat(64)
  it('accepts a round that only records seen crops', () => {
    expect(validRound({ seen: [{ id: 'one', image_sha256: hash }] }).seen).toHaveLength(1)
    expect(validRound({ seen: [{ id: 'one', image_sha256: hash }] }).answers).toHaveLength(0)
  })
  it('refuses an empty round, a crop named twice, or more than 96 crops', () => {
    expect(() => validRound({ answers: [], seen: [] })).toThrow('1–96')
    expect(() => validRound({ answers: [{ id: 'one' }], seen: [{ id: 'one', image_sha256: hash }] })).toThrow('1–96')
    const many = Array.from({ length: 97 }, (_, i) => ({ id: `u${i}`, image_sha256: hash }))
    expect(() => validRound({ seen: many })).toThrow('1–96')
  })
  it('names a corpus glyph by its source revision', () => {
    expect(validRound({ skipped: [{ id: 'codh:1', source_revision: hash }] }).skipped).toHaveLength(1)
    expect(() => validRound({ seen: [{ id: 'codh:1', source_revision: 'nope' }] })).toThrow('image hash')
  })
  it('refuses a seen crop without an image hash, and seen crops outside a round', () => {
    expect(() => validRound({ seen: [{ id: 'one', image_sha256: 'nope' }] })).toThrow('image hash')
    expect(() => validRound({ seen: [{ id: 'one', image_sha256: hash }] }, 'one')).toThrow('Only a round')
  })
})
