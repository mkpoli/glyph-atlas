// BabelStone's description operators, as `glyph_atlas.han_components` reads them: each takes its own
// number of descriptions.
import spellings from '../../../data/vocab/yao-spellings.json';
export const yaoSpelling = (text: string): string | undefined => Object.hasOwn(spellings, text) ? spellings[text as keyof typeof spellings] : undefined;
const BINARY = new Set([...'⿰⿱⿴⿵⿶⿷⿸⿹⿺⿻⿼⿽㇯']), TERNARY = new Set([...'⿲⿳']), UNARY = new Set([...'⿾⿿〾']);
/** How many descriptions `c` takes, 0 for anything that is no operator. */
export const arity = (c: string) => BINARY.has(c) ? 2 : TERNARY.has(c) ? 3 : UNARY.has(c) ? 1 : 0;
/** A description's units: each part no code point names, written as its identifier (`&X-00087;`), and
 *  each other code point. */
export const units = (value: string) => value.match(/&[A-Za-z][A-Za-z0-9+_-]{0,45};|\{[0-9]{1,4}\}|[\s\S]/gu) ?? [];
