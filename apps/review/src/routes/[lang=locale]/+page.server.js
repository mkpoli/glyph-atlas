import { redirect } from '@sveltejs/kit'
import { catalogue, randomSeed, request } from '$lib/client.js'
import { characterAddress } from '$lib/gallery.js'
import { localize } from '$lib/i18n.svelte.js'
import { gallery } from '$lib/layers.js'
import { KEPT, frequent } from '$lib/frequentRuns.js'

const GROUPS = ['all', 'kana', 'kanji', 'hangul', 'gugyeol']
/** How long the homepage waits on one of its own reads before the view loads it itself. */
const READ_TIMEOUT = 15000

// The collection as its view first shows it: one shuffle of the rows the address's search and filters
// select, the progress line, and, with no search or filter, the corpus sample drawn with the same seed
// behind them, and the most frequent pairs above them. The rows are the crops this atlas itself collected and read, and they lead, because a
// corpus record's label is the source's own transcription and is mostly right already.
// A collection that cannot answer leaves the view to load and report it itself, with the same filters.
export async function load({ fetch, params, url }) {
  const q = url.searchParams.get('q')?.trim() ?? '', grapheme = url.searchParams.get('grapheme') ?? '', work = url.searchParams.get('work') ?? ''
  const group = GROUPS.includes(url.searchParams.get('group')) ? url.searchParams.get('group') : 'all'
  // One character, by itself or by code point, has a page of its own.
  const point = /^U\+[0-9a-f]{4,6}$/i.test(q) ? q.toUpperCase() : [...q].length === 1 ? 'U+' + q.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') : null
  if (point) redirect(307, localize(characterAddress(point), params.lang))
  const seed = randomSeed()
  const bare = !q && !grapheme && !work && group === 'all'
  const [result, sample, collection, runs] = await Promise.all([
    // The rows are on the path to the first byte, so a collection that stalls must not hold the page
    // open: the view loads and reports them itself, with the same filters.
    catalogue({ q, grapheme, document: work, group, state: 'all', seed, offset: 0, limit: 60 }, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
    // A sample that cannot answer says so rather than reading as an empty corpus, as the client path
    // does when it draws one after the page is up. `gallery` bounds its own read.
    bare ? gallery(60, seed, { fetch }).catch(error => ({ items: [], status: error?.status === 502 ? 'error' : 'not-loaded' })) : null,
    request('/atlas/collection/status', undefined, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
    // The most frequent pairs, shown above the collection; the view reads them itself if they do not come.
    bare ? frequent('pair', { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).then(items => items.slice(0, KEPT)).catch(() => null) : null,
  ])
  return { explore: { seed, q, grapheme, work, group, collection, result, sample, runs } }
}
