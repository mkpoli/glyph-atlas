// Which font draws a registered figure, a character with an ideographic variation selector. A font
// draws a sequence only when it maps it; any other draws the character without its selector. Three
// fonts map sequences: MJ Figures, the Moji_Joho collection's reference font (`mjFigures.js`), and
// the figure files of Atlas Sans and Atlas Serif (docs/typefaces.md), whose lists say which
// sequences each holds. Atlas Sans has the Adobe-Japan1 sequences alone, so the face a figure takes
// can differ with the face the reader chose.
import { TYPEFACE_VERSION } from '../../../cloudflare/src/typefaceVersions.ts'
import { FAMILY as MJ, drawFigure, figuresOf } from './mjFigures.js'

const BASE = `/atlas/fonts/${TYPEFACE_VERSION}/`
const FAMILIES = { sans: 'Atlas Sans Figures', serif: 'Atlas Serif Figures' }
const FIRST = 0xE0100
const hex = code => code.toString(16).toUpperCase()

const lists = new Map()
/** The sequences of the 256 code points around `code` that `face` draws, as base in hex -> selectors
 *  counted from U+E0100; empty where the face has no file there. A failed read is asked again. */
function listed(face, code) {
  const name = (code >> 8).toString(16).padStart(3, '0'), key = `${face}/${name}`
  if (!lists.has(key)) lists.set(key, fetch(`${BASE}${face}/figures/${name}.json`)
    .then(response => response.ok ? response.json() : response.status === 404 ? {} : Promise.reject(new Error(`${key}: ${response.status}`)))
    .catch(error => { lists.delete(key); throw error }))
  return lists.get(key)
}

const faces = new Map()
/** Load the file of `face` that draws the sequences on `code`, once; a failed load rejects and is tried again next time. */
function load(face, code) {
  const block = code >> 8, name = block.toString(16).padStart(3, '0'), key = `${face}/${name}`
  if (!faces.has(key)) {
    const made = new FontFace(FAMILIES[face], `url(${BASE}${face}/figures/${name}.woff2) format("woff2")`,
      { display: 'swap', unicodeRange: `U+${hex(block << 8)}-${hex((block << 8) + 0xFF)}, U+E0100-E01EF` })
    document.fonts.add(made)
    faces.set(key, made.load().then(() => true, error => { faces.delete(key); document.fonts.delete(made); throw error }))
  }
  return faces.get(key)
}

/** The font family that draws the figure `text` under each face, `{ sans, serif }`, null where no
 *  font maps the sequence: MJ Figures where it does, else the face's own figure file, else under
 *  Atlas Sans the serif one. Rejects when a list or a font could not be read, so the caller keeps
 *  nothing and the next call asks again. */
export async function figureFamilies(text) {
  const [base, selector] = [...text], code = base.codePointAt(0), index = selector.codePointAt(0) - FIRST
  const [registered, sans, serif] = await Promise.all([figuresOf(base), listed('sans', code), listed('serif', code)])
  if (registered.some(figure => figure.char === text)) { await drawFigure(text); return { sans: MJ, serif: MJ } }
  const has = list => list[hex(code)]?.includes(index)
  const [inSans, inSerif] = await Promise.all([has(sans) && load('sans', code), has(serif) && load('serif', code)])
  const drawn = inSerif ? FAMILIES.serif : null
  return { sans: inSans ? FAMILIES.sans : drawn, serif: drawn }
}
