// The registered figures a part of a description can be written with: the variation sequences the
// composer draws (`composer/variants.js`). A sequence its list lacks is not offered, since a description
// holding it could not be drawn.
import { VARIANTS } from './composer/variants.js'
import { figuresOf } from './mjFigures.js'

const SELECTOR = /^[\u{E0100}-\u{E01EF}]$/u
let list = null
// A failed read is asked again next time rather than kept.
const listed = () => list ??= fetch(`${VARIANTS}variants.json`)
  .then(response => response.ok ? response.json() : Promise.reject(new Error(`variants.json: ${response.status}`)))
  .catch(error => { list = null; throw error })

/** The figures of `char` among the `selectors` an export lists for it, as `{ char, mj }`, with the MJ
 *  figure of the sequence or null. A standardized sequence (U+FE00) is no registered figure. */
export const drawnFigures = (char, selectors, registered) => [...selectors ?? ''].filter(selector => SELECTOR.test(selector))
  .map(selector => ({ char: char + selector, mj: registered.find(figure => figure.char === char + selector)?.mj ?? null }))

/** The figures the part `char` can be written with. */
export async function figuresFor(char) {
  if ([...char].length !== 1) return []
  const [variants, registered] = await Promise.all([listed(), figuresOf(char).catch(() => [])])
  return drawnFigures(char, Object.hasOwn(variants, char) ? variants[char] : '', registered)
}
