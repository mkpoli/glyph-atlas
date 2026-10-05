import { redirect } from '@sveltejs/kit'
import { catalogue, randomSeed, request } from '$lib/client.js'
import { characterAddress } from '$lib/gallery.js'
import { localize } from '$lib/i18n.svelte.js'
import { isSingle, pointOf } from '$lib/issues.js'
import { gallery } from '$lib/layers.js'

const GROUPS = ['all', 'kana', 'kanji', 'hangul', 'gugyeol']
/** How long the homepage waits on one of its own reads before the view loads it itself. */
const READ_TIMEOUT = 15000

// The collection as its view first shows it: one shuffle of the rows the address's search and filters
// select, the progress line, and, with no search or filter, the corpus sample drawn with the same seed
// behind them. The rows are the crops this atlas itself collected and read, and they lead, because a
// corpus record's label is the source's own transcription and is mostly right already.
// A collection that cannot answer leaves the view to load and report it itself, with the same filters.
export async function load({ fetch, params, url }) {
  const q = url.searchParams.get('q')?.trim() ?? '', grapheme = url.searchParams.get('grapheme') ?? '', work = url.searchParams.get('work') ?? ''
  const group = GROUPS.includes(url.searchParams.get('group')) ? url.searchParams.get('group') : 'all'
  // One character, by itself or by code point, has a page of its own.
  const point = /^U\+[0-9a-f]{4,6}(\s+U\+[0-9a-f]{4,6})*$/i.test(q) ? q.toUpperCase() : isSingle(q) ? pointOf(q) : null
  if (point) redirect(307, localize(characterAddress(point), params.lang))
  const seed = randomSeed()
  const bare = !q && !grapheme && !work && group === 'all'
  const [result, sample, collection] = await Promise.all([
    // The rows are on the path to the first byte, so a collection that stalls must not hold the page
    // open: the view loads and reports them itself, with the same filters.
    catalogue({ q, grapheme, document: work, group, state: 'all', seed, offset: 0, limit: 60 }, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
    // A sample that cannot answer says so rather than reading as an empty corpus, as the client path
    // does when it draws one after the page is up. `gallery` bounds its own read.
    bare ? gallery(60, seed, { fetch }).catch(error => ({ items: [], status: error?.status === 502 ? 'error' : 'not-loaded' })) : null,
    request('/atlas/collection/status', undefined, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
  ])
  return { explore: { seed, q, grapheme, work, group, collection, result, sample } }
}
