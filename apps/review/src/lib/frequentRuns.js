// The runs Explore shows before anyone asks for them: the most frequent pairs or trigrams of the whole
// collection. They come from the counts the browse panel reads
// (`ngramCounts`), which the Worker answers from one index and keeps at the edge per catalogue
// version; in the browser each list is read once a visit. Only the first `KEPT` of a list travel
// with a page the server renders.
import { ngramCounts } from './ngrams.js'

/** How many runs a page carries: more than it shows at first, so "More" has some to add. */
export const KEPT = 72

const read = new Map()
/** A kind's counts, most frequent first: `[{ text, n, vertical }]`. */
export function frequent(kind, options = {}) {
  if (typeof window === 'undefined') return ngramCounts(kind, '', options).then(found => found.items)
  if (!read.has(kind)) read.set(kind, ngramCounts(kind, '', options).then(found => found.items, error => { read.delete(kind); throw error }))
  return read.get(kind)
}
