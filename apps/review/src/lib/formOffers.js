// Which registered figures and regional shapes a crop's form bar offers beside the character itself.
// Atlas Sans and Atlas Serif draw the ideographs of U+3400–4DB5 and U+4E00–9FEF from Noto Sans and
// Noto Serif CJK JP (docs/typefaces.md), so for those two forms draw what the character's own chip
// already shows: the IVD figure a font gives the character alone (邦 U+90A6 U+E0103, MJ026242), and
// the regional description that holds in Japan (邦's ⿰丰阝 of GHTJKV). Those are set apart as
// `alike`. Any other character is drawn from Plangothic or Jigmo, whose glyphs follow no one region,
// and keeps every form. For every character, a regional shape written as the description of a figure
// the bar offers is that figure (臭's ⿱自犬 is MJ030291), and a shape another character encodes again
// is that character (臭's J ⿱自大 is 𦤀): the bar offers the figure or the character in its place.
// Where the shape is set apart as drawn by the bare character, the character it is goes with it.

/** Whether both site faces draw `char` from Noto CJK JP. */
export const notoDraws = char => {
  const chars = [...char]
  if (chars.length !== 1) return false
  const code = chars[0].codePointAt(0)
  return (code >= 0x3400 && code <= 0x4DB5) || (code >= 0x4E00 && code <= 0x9FEF)
}

/** A 構形 group's forms as they stand on the bar, those built as its own and as each 構形 under it. */
export const formsOf = group => group.blocks.flatMap(block => block.forms)

/** The figures and shapes of `char` the bar offers, and the `alike` ones it leaves out because
 *  another form on the bar stands for them: the bare character, a figure a shape describes, or a
 *  character a shape is. `alike.chars` are the characters drawn as the bare character. */
export function sortForms(char, figures, shapes) {
  const drawn = notoDraws(char)
  const offered = drawn ? figures.filter(figure => !figure.default) : figures
  const described = new Set(offered.map(figure => figure.ids).filter(Boolean))
  const drawnAlike = shape => drawn && shape.regions.includes('J')
  const same = shape => drawnAlike(shape) || described.has(shape.text) || Boolean(shape.chars?.length)
  return {
    figures: offered,
    shapes: shapes.filter(shape => !same(shape)),
    alike: { figures: drawn ? figures.filter(figure => figure.default) : [], shapes: shapes.filter(same),
      chars: shapes.filter(drawnAlike).flatMap(shape => shape.chars ?? []) },
  }
}
