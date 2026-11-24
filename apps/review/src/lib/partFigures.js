// The registered figures a part of a description can be written with: the variation sequences the
// composer draws (`partFiguresData.ts`). A sequence its list lacks is not offered, since a description
// holding it could not be drawn.
import { figuresOf, isFigure } from './mjFigures.js'

// Read with the first part chosen, apart from the editor itself.
const listed = () => import('../../../cloudflare/src/partFiguresData.ts').then(module => module.PART_FIGURES)

/** What a part became, as a title names it: a figure by its sequence, since a title draws it as the bare
 *  character. */
export const partName = part => isFigure(part) ? [...part].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase()).join(' ') : part

/** The figures of `char` among the `selectors` listed for it, as `{ char, mj }`, with the MJ figure of
 *  the sequence or null. */
export const drawnFigures = (char, selectors, registered) => [...selectors ?? '']
  .map(selector => ({ char: char + selector, mj: registered.find(figure => figure.char === char + selector)?.mj ?? null }))

/** The figures the part `char` can be written with. */
export async function figuresFor(char) {
  if ([...char].length !== 1) return []
  const [variants, registered] = await Promise.all([listed(), figuresOf(char).catch(() => [])])
  return drawnFigures(char, Object.hasOwn(variants, char) ? variants[char] : '', registered)
}
