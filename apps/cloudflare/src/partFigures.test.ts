import { describe, expect, it } from 'bun:test';
import { figureForms, partsOf } from './partFigures';

const OWN: Record<string, string> = { 禺: '⿻日禸', 𠤕: '⿱匕矢', 食: '⿱人良', 林: '⿰木木' };
const forms = (description: string) => figureForms(description, part => OWN[part]).map(form => form.char);

describe('the forms a character takes with its parts as registered figures', () => {
  it('change one part, then both, each with how it was made', () => {
    expect(forms('⿺辶禺')).toEqual(['⿺辶\u{E0101}禺', '⿺辶禺\u{E0101}', '⿺辶\u{E0101}禺\u{E0101}']);
    expect(figureForms('⿺辶禺', () => null)[2].route).toEqual([{ was: '辶', became: '辶\u{E0101}' }, { was: '禺', became: '禺\u{E0101}' }]);
  });
  it('go from the largest part to the smallest, opening a part only for one inside it', () => {
    // 匕 is half of 𠤕, a quarter of the whole; 巨 and 音 are halves.
    expect(forms('⿰𠤕巨')).toEqual(['⿰𠤕巨\u{E0101}', '⿰⿱匕\u{E0101}矢巨', '⿰⿱匕\u{E0101}矢巨\u{E0101}']);
    expect(forms('⿱音⿰辶巨').slice(0, 3)).toEqual(['⿱音\u{E0101}⿰辶巨', '⿱音⿰辶\u{E0101}巨', '⿱音⿰辶巨\u{E0101}']);
  });
  it('write a part that occurs twice the same way in both places', () => {
    expect(forms('⿰巨巨')).toEqual(['⿰巨\u{E0101}巨\u{E0101}']);
  });
  it('are none for a lone character, a description cut short, or parts with no figure', () => {
    expect([...forms('巨'), ...forms('⿺辶'), ...forms('⿰木木')]).toEqual([]);
    expect([partsOf('⿺辶禺'), partsOf('禺')]).toEqual([['辶', '禺'], []]);
  });
});
