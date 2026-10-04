import { runOccurrences, runRelated, sortParam } from '$lib/ngrams.js'
import { styleParam } from '$lib/style.js'

// A run's first page of occurrences and the runs near it, rendered with the page; one that cannot be
// read leaves the view to load and report it itself.
export async function load({ fetch, params, url }) {
  const work = url.searchParams.get('work') ?? ''
  const style = styleParam(url.searchParams.get('style') ?? '')
  const sort = sortParam(url.searchParams.get('sort') ?? '')
  const [first, related] = await Promise.all([
    runOccurrences(params.text, { work, style, sort }, { fetch }).catch(() => null),
    runRelated(params.text, { fetch }).catch(() => null),
  ])
  return { text: params.text, work, style, sort, first, related }
}
