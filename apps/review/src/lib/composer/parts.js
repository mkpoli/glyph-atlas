// A saved form writes a part no code point names as its identifier (`&X-00087;`, the top of 合). The
// composer is given the name it draws that part by instead (`customPartDrawings.js`): the part in
// encoded characters (⿵𠆢一), or where it has none BabelStone's number, a CHISE entity or a zi.tools
// chain, which the site's composer export does not draw yet. The table is read the first time a form
// holds such a part.

let names = null
/** `text` as the composer reads it: each part no code point names by the name it draws the part by. */
export async function composerText(text) {
  if (!text.includes('&X-')) return text
  names ??= import('../customPartDrawings.js').then(module => module.default, error => { names = null; throw error })
  const named = await names
  return text.replace(/&X-[0-9]{5};/g, part => named[part.slice(1, -1)] ?? part)
}
