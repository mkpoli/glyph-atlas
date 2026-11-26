import { describe, expect, it } from 'bun:test';
import { expandOdoriji, hasOdoriji, odorijiVariants } from './odoriji';

const chars = (s: string) => [...s];

describe('bidirectional 踊り字 association', () => {
  it('associates unvoiced kana repeats both ways (かか ↔ かゝ / かヽ / か〱)', () => {
    expect(odorijiVariants(chars('かか'))).toEqual(['かゝ', 'かヽ', 'か〱']);
    expect(odorijiVariants(chars('かヽ'))).toEqual(['かか', 'かゝ', 'か〱']);
    expect(odorijiVariants(chars('かゝ'))).toEqual(['かか', 'かヽ', 'か〱']);
    expect(odorijiVariants(chars('い〱'))).toEqual(['いい', 'いゝ', 'いヽ']);
  });

  it('associates voiced kana repeats both ways (かが ↔ かゞ / かヾ / か〲, はば ↔ はゞ / はヾ)', () => {
    expect(odorijiVariants(chars('かが'))).toEqual(['かゞ', 'かヾ', 'か〲']);
    expect(odorijiVariants(chars('かゞ'))).toEqual(['かが', 'かヾ', 'か〲']);
    expect(odorijiVariants(chars('かヾ'))).toEqual(['かが', 'かゞ', 'か〲']);
    expect(odorijiVariants(chars('はゞき'))).toEqual(['はばき', 'はヾき']);
    expect(odorijiVariants(chars('はばき'))).toEqual(['はゞき', 'はヾき']);
    expect(odorijiVariants(chars('うゔ'))).toEqual(['うゞ', 'うヾ', 'う〲']);
  });

  it('handles voiced kana followed by unvoiced or voiced iteration mark (がゝ ↔ がか / がが)', () => {
    expect(odorijiVariants(chars('がゝ'))).toContain('がか');
    expect(odorijiVariants(chars('がゝ'))).toContain('がが');
    expect(odorijiVariants(chars('がか'))).toEqual(['がゝ', 'がヽ']);
    expect(odorijiVariants(chars('がが'))).toEqual(['がゞ', 'がヾ', 'がゝ', 'がヽ']);
    expect(odorijiVariants(chars('がゞ'))).toContain('がが');
  });

  it('associates Han / ideographic iteration marks both ways (人人 ↔ 人々 / 人〻 / 人〃 / 人𖿣)', () => {
    expect(odorijiVariants(chars('人人'))).toEqual(['人々', '人〻', '人〃', '人𖿣']);
    expect(odorijiVariants(chars('人々'))).toEqual(['人人', '人〻', '人〃', '人𖿣']);
    expect(odorijiVariants(chars('人〻'))).toEqual(['人人', '人々', '人〃', '人𖿣']);
    expect(odorijiVariants(chars('人〃'))).toEqual(['人人', '人々', '人〻', '人𖿣']);
    expect(odorijiVariants(chars('人𖿣'))).toEqual(['人人', '人々', '人〻', '人〃']);
    expect(odorijiVariants(chars('の人〻'))).toEqual(['の人人', 'の人々', 'の人〃', 'の人𖿣']);
  });

  it('associates two-character Han repeats (部分部分 ↔ 部分々々 / 部分〻〻)', () => {
    expect(odorijiVariants(chars('部分部分'))).toEqual(['部分々々', '部分〻〻']);
    expect(odorijiVariants(chars('部分々々'))).toEqual(['部分部分', '部分〻〻']);
    expect(odorijiVariants(chars('部分〻〻'))).toEqual(['部分部分', '部分々々']);
  });

  it('associates multi-character くの字点 both ways (つれづれ ↔ つれ〲 / つれ〴〵, つれつれ ↔ つれ〱 / つれ〳〵)', () => {
    expect(odorijiVariants(chars('つれづれ'))).toEqual(['つれ〲', 'つれ〴〵']);
    expect(odorijiVariants(chars('つれ〲'))).toEqual(['つれづれ', 'つれ〴〵']);
    expect(odorijiVariants(chars('つれ〴〵'))).toEqual(['つれづれ', 'つれ〲']);
    expect(odorijiVariants(chars('つれつれ'))).toEqual(['つれ〱', 'つれ〳〵']);
    expect(odorijiVariants(chars('つれ〱'))).toEqual(['つれつれ', 'つれ〳〵']);
    expect(odorijiVariants(chars('つれ〳〵'))).toEqual(['つれつれ', 'つれ〱']);
    expect(odorijiVariants(chars('きり〲す'))).toEqual(['きりぎりす', 'きり〴〵す']);
    expect(odorijiVariants(chars('きりぎりす'))).toEqual(['きり〲す', 'きり〴〵す']);
    // Three-character くの字点 works symmetrically even with prefixes/suffixes.
    expect(odorijiVariants(chars('かきくかきくけ'))).toContain('かきく〱け');
    expect(odorijiVariants(chars('かきく〱け'))).toContain('かきくかきくけ');
  });

  it('associates mixed multi-position voiced/unvoiced repeats both ways (ははすず ↔ はゝすゞ / はヽすヾ)', () => {
    expect(odorijiVariants(chars('ははすず'))).toContain('はゝすゞ');
    expect(odorijiVariants(chars('ははすず'))).toContain('はヽすヾ');
    expect(odorijiVariants(chars('はゝすゞ'))).toContain('ははすず');
    expect(odorijiVariants(chars('ああああ'))).toContain('あゝゝゝ');
    expect(odorijiVariants(chars('あゝゝゝ'))).toContain('ああああ');
  });

  it('associates Tangut (𖿠) and Nushu (𖿡) iteration marks both ways', () => {
    expect(odorijiVariants(chars('𗀀𗀀'))).toEqual(['𗀀𖿠']);
    expect(odorijiVariants(chars('𗀀𖿠'))).toEqual(['𗀀𗀀']);
    expect(odorijiVariants(chars('𛅰𛅰'))).toEqual(['𛅰𖿡']);
    expect(odorijiVariants(chars('𛅰𖿡'))).toEqual(['𛅰𛅰']);
  });

  it('does not invent variants for non-repeating sequences and bounds output size', () => {
    expect(odorijiVariants(chars('んし'))).toEqual([]);
    expect(odorijiVariants(chars('候也'))).toEqual([]);
    expect(odorijiVariants(chars('々'))).toEqual([]);
    expect(odorijiVariants(chars('ああああああああ')).length).toBeLessThanOrEqual(24);
    expect(hasOdoriji(chars('かヽ'))).toBe(true);
    expect(hasOdoriji(chars('かか'))).toBe(false);
    expect(expandOdoriji(chars('つれ〴〵'))).toEqual([chars('つれづれ')]);
  });
});
