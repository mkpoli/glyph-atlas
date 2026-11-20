import { isDescription } from './ids.js'
import { isFigure } from './mjFigures.js'

// Which registered figures and regional shapes a crop's form bar offers beside the character itself.
// Atlas Sans and Atlas Serif draw the ideographs of U+3400–4DB5 and U+4E00–9FEF from Noto Sans and
// Noto Serif CJK JP (docs/typefaces.md), so for those two forms draw what the character's own chip
// already shows: the IVD figure a font gives the character alone (邦 U+90A6 U+E0103, MJ026242), and
// the regional description that holds in Japan (邦's ⿰丰阝 of GHTJKV). Those are set apart as
// `alike`. A Japanese shape with a 構形 of its own (臭 as ⿱自大) is still offered, since filing a
// crop under it files it under that 構形, unless the bar offers the character it describes (𦤀).
// Any other character is drawn from Plangothic or Jigmo,
// whose glyphs follow no one region, and keeps every form.

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
  const same = shape => shape.regions.includes('J') && !('structure' in shape)
  return {
    figures: figures.filter(figure => !figure.default),
    shapes: shapes.filter(shape => !same(shape)),
    alike: { figures: figures.filter(figure => figure.default), shapes: shapes.filter(same) },
  }
}

/** The forms of `forms` the bar offers. A description of another encoded character the bar offers
 *  is that character (⿱自大 is 𦤀), so the bar offers the character alone: the description is that
 *  character's 構形 and stands under it. `structureOf` gives each form's 構形, read as the one it is
 *  built as where a reader folded it into another. `of` is the crop's character: its regional shapes
 *  describe it more finely than its one chip draws it, and a character filed under its 構形 stands
 *  for none of them. `keep` names the forms offered whatever they describe. */
export function withoutDescribed(forms, structureOf, of, keep = []) {
  const own = form => { const structure = structureOf.get(form); return structure && (structure.part ?? structure) }
  const ofId = structureOf.get(of)?.id
  const described = new Map(forms.filter(form => form.char !== of && !isDescription(form.char) && !isFigure(form.char))
    .filter(form => structureOf.get(form.char) && structureOf.get(form.char).id !== ofId)
    .map(form => [own(form.char).label, own(form.char).id]))
  return forms.filter(form => !isDescription(form.char) || keep.includes(form.char)
    || !described.has(form.char) || described.get(form.char) !== own(form.char)?.id)
}
