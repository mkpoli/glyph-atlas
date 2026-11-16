import { DRAWN, DRAWN_FAMILY } from '../../../cloudflare/src/drawnFormsData.ts'

// The drawn forms: ways of writing with no code point of Unicode's, each named by a private-use code
// point of the site's table and drawn by its own font (docs/decisions/0014-drawn-forms.md).

/** The drawn form a text names, `{ for, script, name }` with the head of the grapheme it writes and
 *  what it looks like in words, or null. */
export const drawnForm = text => Object.hasOwn(DRAWN, text ?? '') ? DRAWN[text] : null
/** The font stack that draws a drawn form: only the site's font has a glyph at its code point. */
export const DRAWN_FONT = `"${DRAWN_FAMILY}", serif`
