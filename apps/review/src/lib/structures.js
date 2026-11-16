import { structures as readStructures } from './layers.js'
import { isDescription } from './ids.js'

const HAN = /^\p{Script=Han}/u
/** How many written values one reading asks for. */
export const NAMES = 48
/** A written value that can have a 構形: a Han character or a description. */
export const composable = char => HAN.test(char ?? '') || isDescription(char)

/**
 * The 構形 each written value stands under, by its text: `{ id, label, by, others, part }`, `by` who
 * filed it there (none for one its description gives), `others` the other 構形 readers filed it under
 * (each with the `head` it is folded into) and `part` the 構形 it is built as when a reader folded
 * that into this one. `of` is the character whose regional shapes are among the names. At most NAMES
 * are asked for at once, and a value past them, or one the site knows no structure of, has none here.
 * What is asked again replaces what was `known` of those names.
 */
export async function standing(names, known = new Map(), of = '') {
  const wanted = [...new Set(names)].filter(composable).slice(0, NAMES)
  if (!wanted.length) return known
  const found = await readStructures(wanted.join(''), of)
  return new Map([...[...known].filter(([char]) => !wanted.includes(char)), ...(found.items ?? []).flatMap(group => group.chars.map(char =>
    [char, { id: group.id, label: group.label, by: group.claimed?.[char] ?? null, others: found.filed?.[char] ?? [],
      part: (group.folded ?? []).find(part => part.chars.includes(char)) ?? null }]))])
}
