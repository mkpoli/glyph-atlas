// Written by scripts/build_drawn_forms.py from data/vocab/drawn-forms.yaml; edit neither here.
/** The namespace and version of the site's private-use mapping, and the fonts that draw it. */
export const DRAWN_NAMESPACE = 'glyph-atlas', DRAWN_VERSION = '1';
export const DRAWN_FAMILY = 'Glyph Atlas Drawn Forms', DRAWN_FAMILY_SERIF = 'Glyph Atlas Drawn Forms Serif';
/** Each drawn form's character, with the head of the grapheme it writes, its script and what it looks like. */
export const DRAWN: Record<string, { for: string; script: string; name: string }> = {
  '\u{F0000}': { for: '事', script: 'han', name: "事 in one cursive stroke, a hooked head over a loop that the stroke crosses on its way down to the left; like る" },
  '\u{F0001}': { for: '事', script: 'han', name: "事 as a bar across a long downstroke that ends in a point to the lower left, a narrow loop on its right below the bar; like 方 or 卞" },
  '\u{F0002}': { for: '事', script: 'han', name: "事 as a hooked downstroke with a short dash at its left, an upturned bar and a narrow loop on the right, the tail narrowing to a point at the lower left" },
  '\u{F0003}': { for: '時', script: 'han', name: "時 in cursive, a short left stroke and tilted loop for 日 bridging into a crossed top and sweeping る-like bottom loop for 寺" },
  '\u{F0004}': { for: '時', script: 'han', name: "时 in cursive, a short left stroke and tilted loop for 日 bridging into a horizontal bar and downstroke that loops back up-left for 寸" },
};
