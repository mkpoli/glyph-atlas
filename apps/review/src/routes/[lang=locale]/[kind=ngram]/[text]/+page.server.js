import { ngramOccurrences } from '$lib/ngrams.js'

// A run's first page of occurrences, rendered with the page; one that cannot be read leaves the view to
// load and report it itself.
export async function load({ fetch, params, url }) {
  const work = url.searchParams.get('work') ?? ''
  const first = await ngramOccurrences(params.kind, params.text, { work }, { fetch }).catch(() => null)
  return { kind: params.kind, text: params.text, work, first }
}
