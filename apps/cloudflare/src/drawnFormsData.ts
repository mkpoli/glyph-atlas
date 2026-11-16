// Written by scripts/build_drawn_forms.py from data/vocab/drawn-forms.yaml; edit neither here.
/** The namespace and version of the site's private-use mapping, and the font that draws it. */
export const DRAWN_NAMESPACE = 'glyph-atlas', DRAWN_VERSION = '1', DRAWN_FAMILY = 'Glyph Atlas Drawn Forms';
/** Each drawn form's character, with the head of the grapheme it writes, its script and what it looks like. */
export const DRAWN: Record<string, { for: string; script: string; name: string }> = {
  '\u{F0000}': { for: '事', script: 'han', name: "事 in one cursive stroke, a zigzag head over a wide loop with a tail to the lower left; like る with a larger loop" },
  '\u{F0001}': { for: '事', script: 'han', name: "事 as a bar crossed by two downstrokes, the right one hooking back into the left, which runs on as a tail; like 方 or 阝" },
};
