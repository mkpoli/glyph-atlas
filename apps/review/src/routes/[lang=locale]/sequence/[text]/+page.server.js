import { runOccurrences, runRelated, sortParam } from '$lib/ngrams.js'
import { handParam } from '$lib/hand.js'

// A run's first page of occurrences and the runs near it, rendered with the page; one that cannot be
// read leaves the view to load and report it itself.
export async function load({ fetch, params, url }) {
  const work = url.searchParams.get('work') ?? ''
  const form = url.searchParams.get('form') ?? ''
  const hand = handParam(url.searchParams.get('hand') ?? '')
  const sort = sortParam(url.searchParams.get('sort') ?? '')
  const [first, related] = await Promise.all([
    runOccurrences(params.text, { work, form, hand, sort }, { fetch }).catch(() => null),
    runRelated(params.text, { fetch }).catch(() => null),
  ])
  return { text: params.text, work, form, hand, sort, first, related }
}
