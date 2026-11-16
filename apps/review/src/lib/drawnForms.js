import { DRAWN } from '../../../cloudflare/src/drawnFormsData.ts'

// The drawn forms: ways of writing with no code point of Unicode's, each named by a private-use code
// point of the site's table and drawn by its own fonts (docs/decisions/0014-drawn-forms.md).

/** The drawn form a text names, `{ for, script, name }` with the head of the grapheme it writes and
 *  what it looks like in words, or null. */
export const drawnForm = text => Object.hasOwn(DRAWN, text ?? '') ? DRAWN[text] : null
/** The font that draws a drawn form: the sans cut, or the serif one where the text around it is set
 *  in a serif face (`--drawn-font`, drawn-forms.css and layers.css). */
export const DRAWN_FONT = 'var(--drawn-font)'
