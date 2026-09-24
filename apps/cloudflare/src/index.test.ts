import { describe, it, expect } from 'bun:test';
import { canonical, literal, hira, single } from './index';

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
