// Written by scripts/build_drawn_forms.py from data/vocab/drawn-forms.yaml; edit neither here.
/** The namespace and version of the site's private-use mapping, and the font that draws it. */
export const DRAWN_NAMESPACE = 'glyph-atlas', DRAWN_VERSION = '1', DRAWN_FAMILY = 'Glyph Atlas Drawn Forms';
/** Each drawn form's character, with the head of the grapheme it writes and its script. */
export const DRAWN: Record<string, { for: string; script: string }> = {
  '\u{F0000}': { for: '事', script: 'han' },
  '\u{F0001}': { for: '事', script: 'han' },
};
