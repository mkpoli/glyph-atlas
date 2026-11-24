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
  const items = (await readStructures(names)).items ?? []
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
