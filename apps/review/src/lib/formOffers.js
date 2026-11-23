// Which registered figures and regional shapes a crop's form bar offers beside the character itself.
// Atlas Sans and Atlas Serif draw the ideographs of U+3400–4DB5 and U+4E00–9FEF from Noto Sans and
// Noto Serif CJK JP (docs/typefaces.md), so for those two forms draw what the character's own chip
// already shows: the IVD figure a font gives the character alone (邦 U+90A6 U+E0103, MJ026242), and
// the regional description that holds in Japan (邦's ⿰丰阝 of GHTJKV). Those are set apart as
// `alike`. Any other character is drawn from Plangothic or Jigmo, whose glyphs follow no one region,
// and keeps every form.

/** Whether both site faces draw `char` from Noto CJK JP. */
export const notoDraws = char => {
  const chars = [...char]
  if (chars.length !== 1) return false
  const code = chars[0].codePointAt(0)
  return (code >= 0x3400 && code <= 0x4DB5) || (code >= 0x4E00 && code <= 0x9FEF)
}

/** The figures and shapes of `char` the bar offers, and the `alike` ones it leaves out because
 *  they draw as the bare character. */
export function sortForms(char, figures, shapes) {
  if (!notoDraws(char)) return { figures, shapes, alike: { figures: [], shapes: [] } }
  const same = shape => shape.regions.includes('J')
  return {
    figures: figures.filter(figure => !figure.default),
    shapes: shapes.filter(shape => !same(shape)),
    alike: { figures: figures.filter(figure => figure.default), shapes: shapes.filter(same) },
  }
}
