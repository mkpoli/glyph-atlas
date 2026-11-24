import { ids as readIds, structures as readStructures } from './layers.js'
import { isDescription } from './ids.js'
import { isSeal } from './cardTiers.js'
import { sortForms } from './formOffers.js'

const HAN = /^\p{Script=Han}/u
/** How many written values one reading asks for. */
export const NAMES = 48
/** A written value that can have a 構形: a Han or small seal character, or a description. */
export const composable = char => HAN.test(char ?? '') || isSeal(char) || isDescription(char)
/** Whether a form stands under its 構形 as a proposal: a small seal character has that of its modern
 *  CJK equivalent until a reader files it (decision 0012). `by` is who filed it there. */
export const proposed = (char, by) => isSeal(char) && !by
/** The label of the 構形 another layout of the same parts stands under (⿰辵禺 and ⿺辵禺 under ⿺辶禺), or
 *  null; the Worker's `headOf` answers the same. */
const headLabel = label => /^[⿰⿺]辵/u.test(label) ? '⿺辶' + label.slice(2) : label.startsWith('⿲辵') ? '⿺辶⿰' + label.slice(2) : null
/** How a save names the 構形 a form stands under, by that form (`target`) and as its layout, or null
 *  where that is not it: a reader folded the layout, or the 構形 it is a layout of, into this one. */
export const headTarget = (stands, target) =>
  stands.part && !stands.part.by?.length && stands.label === headLabel(stands.part.label) ? { ...target, head: true } : null
/** What names a block's 構形 of the form bar in a save: an encoded character its description puts there, else
 *  a described form, a small seal character, a regional shape of `of`, or a layout that stands under it, else
 *  its id where a reader's claim has named it. Null where nothing names it. */
export function structureTarget(group, block, structureOf, of) {
  const ranked = [], standing = group.blocks.flatMap(each => each.forms.map(form => [form, structureOf.get(form.char)])).filter(([, stands]) => stands)
  for (const [form, stands] of standing) {
    if (stands.by) continue
    const [rank, target] = form.regions ? [1, { char: form.char, of }] : isSeal(form.char) ? [2, { char: form.char }] : isDescription(form.char) ? [3, { char: form.char }] : [4, { char: form.char }]
    const head = headTarget(stands, target)
    if ((stands.part ?? stands).id === block.id) ranked.push([rank, target])
    else if (stands.id === block.id && head) ranked.push([rank / 10, head])
  }
  if (ranked.length) return ranked.sort((a, b) => b[0] - a[0])[0][1]
  const claimed = standing.some(([, stands]) => stands.by && (stands.part ?? stands).id === block.id || stands.part?.by?.length && [stands.id, stands.part.id].includes(block.id))
  return claimed ? { structure: block.id } : null
}
/** A 構形 group's names as they stand: those built as its own, then each 構形 that stands under it
 *  (`folded`, ⿰辵禺 under ⿺辶禺) with the names built as that. */
export const layouts = group => {
  const folded = (group.folded ?? []).map(part => ({ ...part, chars: part.chars.filter(char => group.chars.includes(char)) })).filter(part => part.chars.length)
  return { own: group.chars.filter(char => !folded.some(part => part.chars.includes(char))), folded }
}

/**
 * The 構形 groups of the names on a character's card, `{ id, label, chars, claimed, folded }` each, and
 * the `twins`: the characters of the group of `char` drawn as `char` itself (𦤀, 臭 encoded again as its
 * J shape). The group leaves out those built as its own; one a reader filed elsewhere stands there.
 */
export async function cardStructures(char, names) {
  const items = (await readStructures(names, '', { open: true })).items ?? []
  const own = items.find(group => group.chars.includes(char))
  if (!own?.chars.some(name => name !== char && /^\p{Script=Han}$/u.test(name))) return { items, twins: [] }
  const alike = await readIds(char).then(found => sortForms(char, [], found.shapes ?? []).alike.chars, () => [])
  const twins = own.chars.filter(name => alike.includes(name))
  return { items: items.map(group => group === own ? { ...group, chars: group.chars.filter(name => !twins.includes(name) || !layouts(group).own.includes(name)) } : group), twins }
}

/**
 * The 構形 each written value stands under, by its text: `{ id, label, by, others, part }`, `by` who
 * filed it there (none for one its description gives), `others` the other 構形 readers filed it under
 * (each with the `head` it is folded into) and `part` the 構形 it is built as when that
 * one stands under this. `of` is the character whose regional shapes are among the names. Requests ask
 * at most NAMES values at a time; a value the site knows no structure of has none here. What is asked
 * again replaces what was `known` of those names.
 */
export async function standing(names, known = new Map(), of = '') {
  const wanted = [...new Set(names)].filter(composable)
  if (!wanted.length) return known
  const chunks = []
  for (let from = 0; from < wanted.length; from += NAMES) chunks.push(wanted.slice(from, from + NAMES))
  const settled = await Promise.allSettled(chunks.map(chunk => readStructures(chunk.join(''), of)))
  if (settled.every(answer => answer.status === 'rejected')) throw settled[0].reason
  // A chunk that failed keeps what was known of its names.
  const asked = new Set(chunks.filter((_, i) => settled[i].status === 'fulfilled').flat())
  const found = settled.filter(answer => answer.status === 'fulfilled').map(answer => answer.value)
  return new Map([
    ...[...known].filter(([char]) => !asked.has(char)),
    ...found.flatMap(answer => (answer.items ?? []).flatMap(group => group.chars.map(char =>
      [char, { id: group.id, label: group.label, by: group.claimed?.[char] ?? null, others: answer.filed?.[char] ?? [],
        part: (group.folded ?? []).find(part => part.chars.includes(char)) ?? null }]))),
  ])
}
