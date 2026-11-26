import { describe, expect, it } from 'bun:test';
import { customPart, drawnWith, spelt, unknownParts } from './customParts';

describe('parts no code point names', () => {
  it('names one identifier by every spelling', () => {
    expect(customPart('{88}')).toBe('X-00087');
    expect(customPart('&X-00087;')).toBe('X-00087');
    expect(customPart('{73}')).toBe('X-00072');
    expect(customPart('&CDP-8BD0;')).toBe('X-00007');
    expect(customPart('{999}')).toBeNull();
    expect(customPart('&X-99999;')).toBeNull();
  });
  it('answers a retired identifier with the one it went into', () => {
    expect(customPart('&X-01653;')).toBe('X-00026');
    expect(customPart('&X-01612;')).toBeNull();
  });
  it('writes every such part as its identifier', () => {
    expect(spelt('⿱{88}龴')).toBe('⿱&X-00087;龴');
    expect(spelt('⿰&CDP-8BD0;寸')).toBe('⿰&X-00007;寸');
    expect(spelt('⿱&X-01653;一')).toBe('⿱&X-00026;一');
    expect(spelt('⿱{999}一')).toBe('⿱{999}一');
    expect(unknownParts('⿱{999}{88}')).toEqual(['{999}']);
  });
  it('writes a description in encoded characters where every part has a drawing', () => {
    expect(drawnWith('⿱&X-00087;龴')).toBe('⿱⿵𠆢一龴');
    expect(drawnWith('令')).toBe('令');
    expect(drawnWith('⿰{5}又')).toBeNull();
    expect(drawnWith('&X-01548;')).toBe('⿰⺀⺀');
    expect(drawnWith('&CDP-8C78;')).toBe('コ');
    expect(unknownParts('{１２}')).toEqual([]);
  });
});

describe('the one spelling', () => {
  it('writes a part of a description that draws one named part as that part', () => {
    expect(spelt('⿱⿵𠆢一龴')).toBe('⿱&X-00087;龴');
    expect(spelt('⿵𠆢一')).toBe('⿵𠆢一');
    expect(spelt('⿱⿴𠀃三八')).toBe('⿱⿴𠀃三八');
    expect(spelt('⿱⿻日乚一')).toBe('⿱⿻日乚一');
  });
  it('writes a part known only as a description as it', () => {
    expect(spelt('⿱&X-01546;一')).toBe('⿱⿰&X-00024;⿶&X-00111;丨一');
    expect(spelt('⿱⿰{24}⿶{112}丨一')).toBe('⿱⿰&X-00024;⿶&X-00111;丨一');
  });
  it('writes a description joined to a named part as that part', () => {
    expect(spelt('⿱&X-01788;一')).toBe('⿱&X-00001;一');
    expect(spelt('⿱⿹⿺㇉一灬一')).toBe('⿱&X-00001;一');
  });
});
