// A saved form writes a part no code point names as its identifier (`&X-00087;`, the top of 合). The
// composer is given the drawing chosen for that part instead (`customPartDrawings.js`): a description in
// encoded characters (⿵𠆢一) or a cut, a character less its other parts (㇯㦲𢦏). A part with no drawing
// that composed well has none, and a form holding it is not handed to the composer at all: it stays
// text. The table is read the first time a form holds such a part.

const PART = /&X-[0-9]{5};/g
let names = null
/** `text` as the composer reads it, each part no code point names by its drawing; null where a part has none. */
export async function composerText(text) {
  if (!text.includes('&X-')) return text
  names ??= import('../customPartDrawings.js').then(module => module.default, error => { names = null; throw error })
  const named = await names
  let missing = false
  const written = text.replace(PART, part => named[part.slice(1, -1)] ?? (missing = true, part))
  return missing ? null : written
}
