import { isSeal, sealsLast } from './cardTiers.js'

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

/**
 * A list's forms under the 構形 each stands under (`structureOf`, by the form's text), a structure where
 * its first form stands, those built as another that stands under it (⿰辵禺 under ⿺辶禺) in a block of
 * their own; the forms with none stand together after them, uncaptioned. The grapheme's own forms come
 * first; a related character (`related`, by its text) joins a 構形 of theirs where it shares one, and
 * otherwise stands after all of them. A small seal character comes last in its block, and after every
 * other form where it has no 構形 or no other form stands under its own. Under a 構形 with a form of the
 * grapheme, a layout of related characters alone follows those with one, and a layout of small seal
 * characters alone comes last. `own` is the id of the 構形 the crop's own form is built as, and
 * `proposed(char, by)` whether a form stands under its 構形 as a proposal. Each group is
 * `{ id, label, blocks: [{ id, label, own, forms }] }`.
 */
export function groupForms(list, { structureOf = new Map(), related = new Set(), own = null, proposed = () => false } = {}) {
  const block = ({ id, label }) => ({ id, label, own: id === own, forms: [] })
  const homes = new Map(), found = []
  const tiers = [['', form => !related.has(form.char) && !isSeal(form.char)], [' related', form => related.has(form.char) && !isSeal(form.char)],
    [' seal', form => isSeal(form.char)]]
  for (const [key, of] of tiers) {
    const loose = []
    for (const form of list) if (of(form)) {
      const structure = structureOf.get(form.char)
      if (!structure) { loose.push(form); continue }
      let home = homes.get(structure.id)
      if (!home) {
        homes.set(structure.id, home = { id: structure.id, label: structure.label, blocks: [block(structure)] })
        found.push(home)
      }
      const layout = structure.part ?? structure
      let built = home.blocks.find(one => one.id === layout.id)
      if (!built) home.blocks.push(built = block(layout))
      built.forms.push({ ...form, proposed: proposed(form.char, structure.by) })
    }
    if (loose.length) found.push({ id: key, label: '', blocks: [{ id: key, label: '', own: false, forms: loose }] })
  }
  const tier = form => tiers.findIndex(([, of]) => of(form))
  const rank = built => built.forms.length ? Math.min(...built.forms.map(tier)) : 0
  for (const group of homes.values()) if (group.blocks.some(built => built.forms.some(form => tier(form) === 0))) group.blocks.sort((a, b) => rank(a) - rank(b))
  return found
}

/** The forms of a grouped bar in the order the number keys 1–0 reach them: as they stand, the small seal
 *  characters after every other, so none takes the key of another form. */
export const keyOrder = groups => sealsLast(groups.flatMap(formsOf))
