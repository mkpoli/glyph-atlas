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
    expect(spelt('⿱⿵𠆢一龴')).toBe('⿱⿵𠆢一龴');
    expect(spelt('⿱{999}一')).toBe('⿱{999}一');
    expect(unknownParts('⿱{999}{88}')).toEqual(['{999}']);
  });
  it('writes a description in encoded characters where every part has a drawing', () => {
    expect(drawnWith('⿱&X-00087;龴')).toBe('⿱⿵𠆢一龴');
    expect(drawnWith('令')).toBe('令');
    expect(drawnWith('⿰{5}又')).toBeNull();
  });
});
