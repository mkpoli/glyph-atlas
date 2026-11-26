// Parts no code point names, as `glyph_atlas.custom_parts` reads them: BabelStone's numbered parts
// (`{88}`, the top of 合), CHISE's entities (`&CDP-8BD0;`) and zi.tools' stroke chains (`#(-𠃌㇉)`),
// each written with the identifier ids-input gives the shape (`&X-00087;`).
import { CUSTOM_PARTS, CUSTOM_PART_DRAWN, CUSTOM_PART_NAMES, CUSTOM_PART_RETIRED } from './customPartsData';

const IN_USE = new Set(CUSTOM_PARTS);
/** One part no code point names, in ids-input's grammar (src/core/custom.js): a number of up to four
 *  digits, an entity, a stroke chain. */
export const CUSTOM_PART = /\{[0-9]{1,4}\}|&[A-Za-z][A-Za-z0-9+_-]{0,45};|#\([^\s()[\]{}#&;]{1,44}\)/gu;
const IDENTIFIER = /^&(X-[0-9]{5});$/;
// A drawing in BabelStone Han PUA's private-use characters, which only that font draws.
const PRIVATE = /[-]/u;

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

/** `text` with each such part written as the first of its drawings that comes out in encoded characters only,
 *  or null where none does (BabelStone Han PUA's private-use glyph is none); a drawing may name such a part. */
export function drawnWith(text: string, depth = 0): string | null {
  if (depth > 8) return null;
  let missing = false;
  const out = text.replace(CUSTOM_PART, name => {
    const key = customPart(name), draws = key && Object.hasOwn(CUSTOM_PART_DRAWN, key) ? CUSTOM_PART_DRAWN[key] : [];
    for (const draw of draws) {
      const written = PRIVATE.test(draw) ? null : drawnWith(draw, depth + 1);
      if (written !== null) return written;
    }
    missing = true;
    return name;
  });
  return missing ? null : out;
}
