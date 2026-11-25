// Parts no code point names, as `glyph_atlas.custom_parts` reads them: BabelStone's numbered parts
// (`{88}`, the top of 合), CHISE's entities (`&CDP-8BD0;`) and zi.tools' stroke chains (`#(-𠃌㇉)`),
// each written with the identifier ids-input gives the shape (`&X-00087;`).
import { CUSTOM_PARTS, CUSTOM_PART_DRAWN, CUSTOM_PART_NAMES, CUSTOM_PART_RETIRED } from './customPartsData';

const IN_USE = new Set(CUSTOM_PARTS);

/** One part no code point names, in any of the three spellings or as an identifier. */
export const CUSTOM_PART = /\{\d+\}|&[A-Za-z0-9+._-]+;|#\([^()\s]*\)/gu;
const IDENTIFIER = /^&(X-\d{5});$/;

/** The identifier a part's name, or an identifier itself, stands for (`{88}` and `&X-00087;` are
 *  `X-00087`); one merged into another answers that one, and an unknown or withdrawn one null. */
export function customPart(name: string): string | null {
  if (Object.hasOwn(CUSTOM_PART_NAMES, name)) return CUSTOM_PART_NAMES[name];
  const key = IDENTIFIER.exec(name)?.[1];
  if (!key) return null;
  if (Object.hasOwn(CUSTOM_PART_RETIRED, key)) return CUSTOM_PART_RETIRED[key] || null;
  return IN_USE.has(key) ? key : null;
}

/** `text` with every part no code point names written as its identifier (`⿱{88}龴` is `⿱&X-00087;龴`);
 *  one the table does not know stays as it was typed. */
export const spelt = (text: string) => text.replace(CUSTOM_PART, name => { const key = customPart(name); return key ? `&${key};` : name });

/** The parts of `text` no code point names that the table does not know. */
export const unknownParts = (text: string) => [...text.matchAll(CUSTOM_PART)].map(found => found[0]).filter(name => customPart(name) === null);

/** `text` with each such part written as what draws it, in encoded characters only, or null where a part
 *  has no drawing in them (BabelStone Han PUA's private-use glyph is none); a drawing may name such a part. */
export function drawnWith(text: string, depth = 0): string | null {
  if (depth > 8) return null;
  let missing = false;
  const out = text.replace(CUSTOM_PART, name => {
    const key = customPart(name), draw = key && Object.hasOwn(CUSTOM_PART_DRAWN, key) ? CUSTOM_PART_DRAWN[key] : '';
    const written = draw && !/[\uE000-\uF8FF]/u.test(draw) ? drawnWith(draw, depth + 1) : null;
    if (written === null) { missing = true; return name; }
    return written;
  });
  return missing ? null : out;
}
