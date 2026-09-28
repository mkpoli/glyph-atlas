import { pairOccurrences } from '$lib/pairs.js'

// A pair's first page of occurrences, rendered with the page; one that cannot be read leaves the view to
// load and report it itself.
export async function load({ fetch, params, url }) {
  const work = url.searchParams.get('work') ?? ''
  const first = await pairOccurrences(params.text, { work }, { fetch }).catch(() => null)
  return { text: params.text, work, first }
}
