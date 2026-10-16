// The characters a crop's look-alikes are filed under, offered beside the recognisers' suggestions.
// It holds no interface strings, so tests import it bare.
import { isUnassigned } from './identity.js'

const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' })

/** The characters of a `/similar` answer other than `label`, most alike first: each scores the sum of
 *  its crops' scores over both lists, so a character several neighbours share outranks one close crop. */
export function lookAlikeLabels(result, label, limit = 4) {
  const seen = new Set(), totals = new Map()
  for (const item of [...(result?.similar ?? []), ...(result?.filed_differently ?? [])]) {
    if (seen.has(item.id)) continue
    seen.add(item.id)
    const text = item.label
    if (!text || text === label || isUnassigned(item) || [...graphemes.segment(text)].length !== 1) continue
    totals.set(text, (totals.get(text) ?? 0) + item.score)
  }
  return [...totals].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([text]) => text)
}
