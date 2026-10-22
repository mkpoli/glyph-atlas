import { redirect } from '@sveltejs/kit'
import { catalogue, request, shuffleSeed } from '$lib/client.js'
import { characterAddress } from '$lib/gallery.js'
import { keyText } from '$lib/codePoints.js'
import { localize } from '$lib/i18n.svelte.js'
import { hasCharacterPage, pointOf } from '$lib/issues.js'
import { KEPT, frequent } from '$lib/frequentRuns.js'

const GROUPS = ['all', 'kana', 'kanji', 'hangul', 'gugyeol', 'tangut']
/** How long the homepage waits on one of its own reads before the view loads it itself. */
const READ_TIMEOUT = 15000

// The collection as its view first shows it: the progress line, the most frequent pairs above it, and,
// for a search, a grapheme or a work, one shuffle of the rows they select. Unnarrowed, the rows and the
// corpus sample behind them are a shuffle of the whole collection, whose crops lie on dozens of pages
// each read whole, so the page does not wait for them: the view deals them with the seed rendered here
// once it shows, with placeholders until they come. The rows are the crops this atlas itself collected
// and read, and they lead, because a corpus record's label is the source's own transcription and is
// mostly right already. A collection that cannot answer leaves the view to load and report it itself,
// with the same filters.
export async function load({ fetch, params, url }) {
  const q = url.searchParams.get('q')?.trim() ?? '', grapheme = url.searchParams.get('grapheme') ?? '', work = url.searchParams.get('work') ?? ''
  const group = GROUPS.includes(url.searchParams.get('group')) ? url.searchParams.get('group') : 'all'
  // One character, by itself or by code point, has a page of its own.
  const written = keyText(q) ?? q
  const point = hasCharacterPage(written) ? pointOf(written) : null
  if (point) redirect(307, localize(characterAddress(point), params.lang))
  const seed = shuffleSeed()
  const sampled = !q && !grapheme && !work, bare = sampled && group === 'all'
  const [result, collection, runs] = await Promise.all([
    // The rows are on the path to the first byte, so a collection that stalls must not hold the page
    // open: the view loads and reports them itself, with the same filters.
    sampled ? null : catalogue({ q, grapheme, document: work, group, state: 'all', seed, limit: 60 }, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
    request('/atlas/collection/status', undefined, { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).catch(() => null),
    // The most frequent pairs, shown above the collection; the view reads them itself if they do not come.
    bare ? frequent('pair', { fetch, signal: AbortSignal.timeout(READ_TIMEOUT) }).then(items => items.slice(0, KEPT)).catch(() => null) : null,
  ])
  return { explore: { seed, q, grapheme, work, group, collection, result, runs } }
}
