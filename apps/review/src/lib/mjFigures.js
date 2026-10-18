// The registered figures of a character, the Moji_Joho sequences of the IVD (所 has 所󠄂 and 所󠄃), and
// the font that draws them, MJ Figures. scripts/build_mj_figures_font.py cuts both into files of a few
// dozen code points (`/fonts/mj/`), so a character reads its own file and no other.
export const FAMILY = 'MJ Figures'
const SELECTOR = /^[\u{E0100}-\u{E01EF}]$/u

let index = null
const read = url => fetch(url).then(response => response.ok ? response.json() : Promise.reject(new Error(`${url}: ${response.status}`)))
// A failed read is asked again next time rather than kept.
const indexed = () => index ??= read('/fonts/mj/figures.json').catch(error => { index = null; throw error })
async function fileOf(char) {
  const code = char.codePointAt(0)
  return (await indexed()).files.find(([, first, last]) => code >= first && code <= last) ?? null
}

/** Whether `text` is one character with an ideographic variation selector. */
export const isFigure = text => { const chars = [...text]; return chars.length === 2 && SELECTOR.test(chars[1]) }

const figures = new Map()
/** The registered sequences of `char`, as `{ char, mj, default }`, the default figure (the one the
 *  font draws for `char` alone) marked; empty for a character with one figure or none. */
export async function figuresOf(char) {
  if ([...char].length !== 1) return []
  const file = await fileOf(char)
  if (!file) return []
  const [name] = file
  if (!figures.has(name)) figures.set(name, read(`/fonts/mj/${name}.json`).catch(error => { figures.delete(name); throw error }))
  return ((await figures.get(name))[char] ?? []).map(([text, mj, isDefault]) => ({ char: text, mj, default: isDefault }))
}

const faces = new Map()
/** Load the font file that draws the sequences on `text`'s base character; rejects when it cannot be
 *  loaded, and the next call tries again. */
export function drawFigure(text) {
  return fileOf(text).then(file => {
    if (!file) throw new Error(`no font draws ${text}`)
    const [name, first, last] = file
    if (!faces.has(name)) {
      const hex = code => code.toString(16).toUpperCase()
      const face = new FontFace(FAMILY, `url(/fonts/mj/${name}.woff2) format("woff2")`,
        { display: 'swap', unicodeRange: `U+${hex(first)}-${hex(last)}, U+E0100-E01EF` })
      document.fonts.add(face)
      faces.set(name, face.load().catch(error => { faces.delete(name); document.fonts.delete(face); throw error }))
    }
    return faces.get(name)
  })
}
