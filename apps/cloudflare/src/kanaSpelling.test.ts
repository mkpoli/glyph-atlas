import { describe, it, expect } from 'bun:test';
import { modernSpellings } from './kanaSpelling';

describe('a reading in historical kana', () => {
  it('is read in the modern spelling at each step of 現代仮名遣い', () => {
    expect(modernSpellings('さふらふ')).toContain('そうろう');
    expect(modernSpellings('さうらふ')).toContain('そうろう');
    expect(modernSpellings('おもふ')).toContain('おもう');
    expect(modernSpellings('あはれ')).toContain('あわれ');
    expect(modernSpellings('かうべ')).toContain('こうべ');
    expect(modernSpellings('てふ')).toContain('ちょう');
    expect(modernSpellings('けふ')).toContain('きょう');
    expect(modernSpellings('しやう')).toContain('しょう');
    expect(modernSpellings('きやく')).toContain('きゃく');
    expect(modernSpellings('くわん')).toContain('かん');
    expect(modernSpellings('をとこ')).toContain('おとこ');
    expect(modernSpellings('ゐる')).toContain('いる');
    expect(modernSpellings('いふ')).toEqual(expect.arrayContaining(['いう', 'ゆう']));
  });
  it('keeps a spelling that stops before the long vowel', () => {
    // あふ is あう (会う) before あう could be read おう.
    expect(modernSpellings('あふ')[0]).toBe('あう');
  });
  it('reads the correspondences 現代仮名遣い lists for ぢ, ふ, くゎ and ゃう', () => {
    expect(modernSpellings('でふ')).toContain('じょう');
    expect(modernSpellings('でう')).toContain('じょう');
    expect(modernSpellings('あふぐ')).toContain('あおぐ');
    expect(modernSpellings('たふれる')).toContain('たおれる');
    expect(modernSpellings('くゎん')).toContain('かん');
    expect(modernSpellings('しゃう')).toContain('しょう');
  });
  it('keeps the written-out spelling before the は-row step', () => {
    expect(modernSpellings('はゝ')).toContain('はは');
    expect(modernSpellings('つゞみ')).toContain('つづみ');
  });
  it('writes out ゝ and ゞ', () => {
    expect(modernSpellings('すヾ')).toContain('すず');
    expect(modernSpellings('さヽ')).toContain('ささ');
    expect(modernSpellings('さゝ')).toContain('ささ');
    expect(modernSpellings('すゞ')).toContain('すず');
    expect(modernSpellings('はゞかる')).toContain('はばかる');
  });
  it('offers nothing new for a modern reading with no historical spelling', () => {
    expect(modernSpellings('とき')).toEqual([]);
    expect(modernSpellings('こう')).toEqual([]);
    expect(modernSpellings('しょう')).toEqual([]);
  });
  it('leaves the first kana of a は-row reading as it is', () => {
    expect(modernSpellings('ほふ')).toContain('ほう');
    expect(modernSpellings('ひと')).toEqual([]);
  });
});
