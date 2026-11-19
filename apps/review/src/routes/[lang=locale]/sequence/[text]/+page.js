import { sortParam } from '$lib/ngrams.js'
import { handParam } from '$lib/hand.js'

// The run and the narrowing its address asks for. The page reads its occurrences and the runs near it
// once it is open in a browser, so an address that is only fetched costs no reading of the corpus.
export function load({ params, url }) {
  return {
    text: params.text,
    work: url.searchParams.get('work') ?? '',
    form: url.searchParams.get('form') ?? '',
    hand: handParam(url.searchParams.get('hand') ?? ''),
    sort: sortParam(url.searchParams.get('sort') ?? ''),
  }
}
