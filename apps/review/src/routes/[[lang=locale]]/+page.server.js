import { redirect } from '@sveltejs/kit'
import { catalogue, randomSeed, request } from '$lib/client.js'
import { characterAddress } from '$lib/gallery.js'
import { localize } from '$lib/i18n.svelte.js'
import { gallery } from '$lib/layers.js'

const GROUPS = ['all', 'kana', 'kanji', 'hangul', 'gugyeol']

// The collection as its view first shows it: one shuffle of the rows the address's search and filters
// select, the progress line, and, with no search or filter, a corpus sample drawn with the same seed.
// A collection that cannot answer leaves the view to load and report it itself, with the same filters.
export async function load({ fetch, params, url }) {
  const q = url.searchParams.get('q')?.trim() ?? '', grapheme = url.searchParams.get('grapheme') ?? '', work = url.searchParams.get('work') ?? ''
  const group = GROUPS.includes(url.searchParams.get('group')) ? url.searchParams.get('group') : 'all'
  // One character, by itself or by code point, has a page of its own.
  const point = /^U\+[0-9a-f]{4,6}$/i.test(q) ? q.toUpperCase() : [...q].length === 1 ? 'U+' + q.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') : null
  if (point) redirect(307, localize(characterAddress(point), params.lang ?? 'en'))
  const seed = randomSeed()
  const bare = !q && !grapheme && !work && group === 'all'
  const [result, sample, collection] = await Promise.all([
    catalogue({ q, grapheme, document: work, group, state: 'all', seed, offset: 0, limit: 60 }, { fetch }).catch(() => null),
    bare ? gallery(60, seed, { fetch }).catch(() => null) : null,
    request('/atlas/collection/status', undefined, { fetch }).catch(() => null),
  ])
  return { explore: { seed, q, grapheme, work, group, result, sample, collection } }
}
