import { sealsLast } from './cardTiers.js'
import { formsOf, sortForms } from './formOffers.js'
import { isFigure } from './mjFigures.js'

// The forms a crop of a grapheme (字種) with no character of its own may be, from what the site answers of the
// whole grapheme (`layers.graphemeForms`) and the figures the client holds of each member.

const pointOf = char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
// The language whose fonts draw a BabelStone region's shape (U+6236 戶 is drawn as 戸 under `ja`).
const REGION_LANG = { G: 'zh-Hans', H: 'zh-HK', M: 'zh-MO', T: 'zh-Hant', J: 'ja', K: 'ko', P: 'ko-KP', V: 'vi' }
export const langOf = regions => [...regions].map(region => REGION_LANG[region]).find(Boolean) ?? ''

/**
 * The bar of a grapheme: `chips`, the members in the order they are to stand (`focal` first, the characters
 * its source names it by, and the small seal characters last), each followed by its registered figures
 * (`figures`, by member) and its regional shapes; then the variants of any member, those of a focal one
 * first, and the forms the grapheme's crops carry. Every chip names the member choosing it assigns
 * (`base`): itself, a figure's or a shape's own, and none for a shape two members share, a character
 * outside the grapheme or a description of none. What stands for a form the bar leaves out is in `alike`,
 * as `sortForms` sets it apart member by member. `related` and `derived` are the focal members', for the picker, `bases` each
 * offered form's base and `shaped` the members with regional shapes on the bar.
 */
export function graphemeChips(found, figures, focal = []) {
  const family = found.members.map(member => member.char), own = new Set(family)
  const first = [...new Set([...focal, found.head])].filter(char => own.has(char))
  const ordered = sealsLast([...first.map(char => found.members.find(member => member.char === char)), ...found.members.filter(member => !first.includes(member.char))])
  const sorted = new Map(ordered.map(member => [member.char, sortForms(member.char, figures.get(member.char) ?? [], member.shapes)]))
  const twins = new Set([...sorted.values()].flatMap(forms => forms.alike.chars).filter(char => !first.includes(char)))
  const figure = (member, { char, mj }) => ({ char, code_point: pointOf(char), script: '', mj, base: member.char, of: member.char })
  const shape = (member, { text, regions, base }) => ({ char: text, code_point: null, script: '', encoded: false, regions, lang: langOf(regions), base, of: base })
  const entry = member => ({ char: member.char, code_point: member.code_point, script: member.script ?? '', base: member.char, of: member.char })
  const alike = ordered.flatMap(member => {
    const apart = sorted.get(member.char).alike
    return [...apart.figures.map(each => figure(member, each)), ...apart.shapes.map(each => shape(member, each)), ...twins.has(member.char) ? [entry(member)] : []]
  })
  const apart = new Set(alike.map(form => form.char))
  const ofFocal = row => row.via.some(char => first.includes(char))
  const outside = row => ({ char: row.char, code_point: row.code_point, script: '', base: null, of: null })
  const carried = form => ({ char: form.char, code_point: null, script: 'han', lang: '', base: isFigure(form.char) && own.has([...form.char][0]) ? [...form.char][0] : null, of: null })
  const seen = new Set()
  const chips = [
    ...ordered.filter(member => !twins.has(member.char)).flatMap(member => [entry(member),
      ...sorted.get(member.char).figures.map(each => figure(member, each)), ...sorted.get(member.char).shapes.map(each => shape(member, each))]),
    ...[...found.variants.filter(ofFocal), ...found.variants.filter(row => !ofFocal(row))].map(outside),
    ...found.forms.filter(form => !apart.has(form.char)).map(carried),
  ].filter(chip => !seen.has(chip.char) && seen.add(chip.char))
  const bases = new Map([...alike, ...chips].map(chip => [chip.char, chip.base]))
  const picked = rows => rows.filter(row => ofFocal(row) && !seen.has(row.char))
  return { of: found.head, chips, alike, family, bases, focal: first,
    shaped: ordered.filter(member => chips.some(chip => chip.regions && chip.of === member.char)).map(member => member.char),
    related: picked(found.related).map(outside),
    derived: picked(found.derived).map(row => ({ ...outside(row), encoded: row.encoded, routes: [row.route.map(([was, became]) => ({ was, became }))] })) }
}

/** A bar no form has been read for. */
export const NO_FORMS = { members: [], variants: [], related: [], derived: [], alike: [] }
/** What a crop's bar is read for, as one string: its character `char`, or with no character of its own (`whole`) the
 *  crop itself, and the characters its source names its grapheme by (`focal`). */
export const barKey = (id, char, whole, focal) => JSON.stringify(whole ? [char, id, focal] : [char])
const alone = char => ({ ...NO_FORMS, of: char, chips: [{ char, code_point: pointOf(char), script: '', base: char, of: char }], family: [char], bases: new Map([[char, char]]), focal: [char], shaped: [] })
/** What a crop's bar lists of the forms last read (`offered`): those read for its `key`, or until then its character
 *  `char` alone. A crop with no character (`whole`) lists nothing until then, and is not `ready`: what choosing a
 *  form assigns is not known. One given another character (`subject`) lists that character until then. */
export function listedOf(offered, key, char, whole, subject = null) {
  const read = offered.key === key
  return { ready: read || !whole, listed: read ? offered : subject ? alone(subject) : { ...NO_FORMS, members: whole ? [] : [{ char }] } }
}

/** The 構形 groups a bar of more than `limit` chips shows, and those it folds away: every group with a form in
 *  `keep` or a 構形 in `structures`, by id, and of the rest as many from the start as fit, whole. */
export function foldGroups(groups, { keep = new Set(), structures = new Set(), limit = 40 } = {}) {
  const size = group => formsOf(group).length
  const kept = group => formsOf(group).some(form => keep.has(form.char)) || group.blocks.some(block => structures.has(block.id))
  if (groups.reduce((n, group) => n + size(group), 0) <= limit) return { shown: groups, hidden: [] }
  let n = groups.filter(kept).reduce((sum, group) => sum + size(group), 0), full = false
  const shown = new Set(groups.filter(group => kept(group) || !full && (n + size(group) <= limit ? (n += size(group), true) : (full = true, false))))
  return { shown: groups.filter(group => shown.has(group)), hidden: groups.filter(group => !shown.has(group)) }
}
