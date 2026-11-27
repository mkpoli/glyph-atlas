// A saved form writes a part no code point names as its identifier (`&X-00087;`, the top of 合). A form, or a
// piece of one, that is exactly how BabelStone IDS or CHISE IDS describe a character is given to the composer
// as that character, so it draws as the character's own glyph, as ⿰女子 draws as 好. Each part left is given
// the drawing chosen for it (`customPartDrawings.js`): the composer's own cut of it, or a drawing that
// composed well in characters holding it. A part with neither is an empty slot (⬚), which the drawing shows
// as the editor's placeholder in the box the part takes. The table is read the first time a form holds such
// a part.
import { EMPTY, arity, units } from '../ids.js'

const PART = /&X-[0-9]{5};/g
let table = null

/** `text` with every piece of it that is exactly a character's description written as that character, the
 *  largest first; a text that is no one description is left as it is. */
function asCharacters(text, characters) {
  const all = units(text)
  let at = 0
  const read = () => {
    const start = at, unit = all[at++]
    if (unit === undefined) throw new RangeError(text)
    const parts = Array.from({ length: arity(unit) }, () => read())
    return characters[all.slice(start, at).join('')] ?? unit + parts.join('')
  }
  try {
    const out = read()
    return at === all.length ? out : text
  } catch {
    return text
  }
}

/** `text` as the composer reads it, each part no code point names by its drawing, or an empty slot where it has none. */
export async function composerText(text) {
  if (!text.includes('&X-')) return text
  table ??= import('../customPartDrawings.js').then(module => module, error => { table = null; throw error })
  const { default: named, characters } = await table
  return asCharacters(text, characters).replace(PART, part => named[part.slice(1, -1)] ?? EMPTY)
}
