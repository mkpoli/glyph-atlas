import { describe, expect, it } from 'bun:test';
import { figureForms, partsOf } from './partFigures';

const OWN: Record<string, string> = { 禺: '⿻日禸', 𠤕: '⿱匕矢', 食: '⿱人良', 林: '⿰木木' };
const forms = (description: string) => figureForms([description], part => OWN[part]).map(form => form.char);

describe('the forms a character takes with its parts as registered figures', () => {
  it('change one part, then both, each with how it was made', () => {
    expect(forms('⿺辶音')).toEqual(['⿺辶\u{E0101}音', '⿺辶音\u{E0101}', '⿺辶\u{E0101}音\u{E0101}']);
    expect(figureForms(['⿺辶禺'], () => null).at(-1)!.route).toEqual([{ was: '辶', became: '辶\u{E0101}' }, { was: '禺', became: '禺\u{E0101}' }]);
  });
  it('go from the largest part to the smallest, opening a part only for one inside it', () => {
    // 匕 is half of 𠤕, a quarter of the whole; 巨 and 音 are halves.
    expect(forms('⿰𠤕巨')).toEqual(['⿰𠤕巨\u{E0101}', '⿰⿱匕\u{E0101}矢巨', '⿰⿱匕\u{E0101}矢巨\u{E0101}']);
    expect(forms('⿱音⿰辶巨').slice(0, 3)).toEqual(['⿱音\u{E0101}⿰辶巨', '⿱音⿰辶\u{E0101}巨', '⿱音⿰辶巨\u{E0101}']);
  });
  it('write a part that occurs twice the same way in both places', () => {
    expect(forms('⿰巨巨')).toEqual(['⿰巨\u{E0101}巨\u{E0101}']);
  });
  it('leave out a description a registered figure of the whole character has', () => {
    // 遇󠄃 (U+9047 U+E0103, MJ026028) is ⿺辶禺󠄁.
    expect(forms('⿺辶禺')).toEqual(['⿺辶\u{E0101}禺', '⿺辶\u{E0101}禺\u{E0101}']);
  });
  it('are ordered over every description, and take none from one that writes out parts of another', () => {
    const of = (...descriptions: string[]) => figureForms(descriptions, part => OWN[part]).map(form => form.char);
    expect(of('⿰亻⿱木巨', '⿰亻巨')).toEqual(['⿰亻巨\u{E0101}', '⿰亻⿱木巨\u{E0101}']);
    expect(of('⿰𠤕巨', '⿰⿱匕矢巨')).toEqual(forms('⿰𠤕巨'));
    expect(of('⿰⿱匕矢巨', '⿰𠤕巨')).toEqual(forms('⿰𠤕巨'));
  });
  it('write a figure inside one part where another part holding it is a figure itself', () => {
    const own: Record<string, string> = { 分: '⿱八刀', 貝: '⿱目八' };
    expect(figureForms(['⿱分貝'], part => own[part]).map(form => form.char)).toContain('⿱分\u{E0101}⿱目八\u{E0101}');
  });
  it('are none for a lone character, a description cut short, or parts with no figure', () => {
    expect([...forms('巨'), ...forms('⿺辶'), ...forms('⿰木木')]).toEqual([]);
    expect([partsOf('⿺辶禺'), partsOf('禺')]).toEqual([['辶', '禺'], []]);
  });
});
