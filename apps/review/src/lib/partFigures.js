// The registered figures a part of a description can be written with: the variation sequences the
// current composer export draws (`han-compose/variants`). A sequence its list lacks is not offered,
// since a description holding it could not be drawn.
import { grassIVS, grassShape, variationSequences } from 'han-compose/variants'
import { isNode, partAt, write } from './ids.js'
import { composerBase } from './composer/base.js'
import { figuresOf } from './mjFigures.js'

export { grassIVS, grassShape }
const SELECTOR = /^[\u{E0100}-\u{E01EF}]$/u

/** Every character in the current export with the variation sequences it draws. */
export const variationSequenceMap = () => variationSequences(composerBase())

/** The figures of `char` among the `selectors` (or full sequences) an export lists for it, as
 *  `{ char, mj }`, with the MJ figure of the sequence or null. A standardized sequence (U+FE00) is
 *  no registered figure. */
export function drawnFigures(char, selectors, registered = []) {
  const list = Array.isArray(selectors)
    ? selectors.map(sequence => [...sequence].slice(1).join(''))
    : [...selectors ?? '']
  return list.filter(selector => SELECTOR.test(selector))
    .map(selector => ({ char: char + selector, mj: registered.find(figure => figure.char === char + selector)?.mj ?? null }))
}

/** All selectable forms of a component `char` (the plain character followed by its drawn IVS
 *  figures), or an empty list when the export draws no IVS for it. */
export function componentVariantForms(char, sequences, registered = []) {
  if ([...char ?? ''].length !== 1) return []
  const ivs = drawnFigures(char, sequences, registered)
  return ivs.length ? [{ char, mj: null }, ...ivs] : []
}

/** The active grass radical shape (`艹`, `⺿`, or `⻀`) for the chosen part of `tree`, or null when
 *  the chosen part is not a grass radical (including `⿰十十` at the top of `⿱`/`⿳`). */
export function grassForm(picked, chosen, tree) {
  if (typeof picked === 'string') return grassShape(picked) ?? null
  if (!isNode(picked) || write(picked) !== '⿰十十' || chosen?.at(-1) !== 0) return null
  const parent = partAt(tree, chosen.slice(0, -1))
  return isNode(parent) && ['⿱', '⿳'].includes(parent.op) ? '⺿' : null
}

/** The figures the part `char` can be written with. */
export async function figuresFor(char) {
  if ([...char].length !== 1) return []
  const [variants, registered] = await Promise.all([variationSequenceMap(), figuresOf(char).catch(() => [])])
  return drawnFigures(char, variants.get(char) ?? [], registered)
}
