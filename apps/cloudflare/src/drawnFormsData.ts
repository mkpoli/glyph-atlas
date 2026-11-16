// Written by scripts/build_drawn_forms.py from data/vocab/drawn-forms.yaml; edit neither here.
/** The namespace and version of the site's private-use mapping, and the font that draws it. */
export const DRAWN_NAMESPACE = 'glyph-atlas', DRAWN_VERSION = '1', DRAWN_FAMILY = 'Glyph Atlas Drawn Forms';
/** Each drawn form's character, with the head of the grapheme it writes, its script and what it looks like. */
export const DRAWN: Record<string, { for: string; script: string; name: string }> = {
  '\u{F0000}': { for: '事', script: 'han', name: "事 in one cursive stroke, a hooked head over a loop that the stroke crosses on its way down to the left; like る" },
  '\u{F0001}': { for: '事', script: 'han', name: "事 as a short bar on a long downstroke, a bowl on its right from under the bar, the tail hooking left; like 方 or 卞" },
};
