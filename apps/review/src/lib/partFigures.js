// The registered figures a part of a description can be written with: the variation sequences the
// current composer export draws (`han-compose/variants`). A sequence its list lacks is not offered,
// since a description holding it could not be drawn.
import { variationSequences } from 'han-compose/variants'
import { composerBase } from './composer/config.js'
import { figuresOf } from './mjFigures.js'

const SELECTOR = /^[\u{E0100}-\u{E01EF}]$/u

/** Every character in the current export with the variation sequences it draws. */
export const variationSequenceMap = () => variationSequences(composerBase())

/** The figures of `char` among the `selectors` (or full sequences) an export lists for it, as
 *  `{ char, mj }`, with the MJ figure of the sequence or null. A standardized sequence (U+FE00) is
 *  no registered figure. */
export function drawnFigures(char, selectors, registered) {
  const list = Array.isArray(selectors)
    ? selectors.map(sequence => [...sequence].slice(1).join(''))
    : [...selectors ?? '']
  return list.filter(selector => SELECTOR.test(selector))
    .map(selector => ({ char: char + selector, mj: registered.find(figure => figure.char === char + selector)?.mj ?? null }))
}

/** The figures the part `char` can be written with. */
export async function figuresFor(char) {
  if ([...char].length !== 1) return []
  const [variants, registered] = await Promise.all([variationSequenceMap(), figuresOf(char).catch(() => [])])
  return drawnFigures(char, variants.get(char) ?? [], registered)
}
