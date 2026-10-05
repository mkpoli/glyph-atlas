import { request } from './client.js'
import { t } from './i18n.svelte.js'
import { handParam } from './hand.js'

// Runs of characters that follow each other on a line: the pair and trigram counts Explore lists, in one
// work or all, and the page of any run's occurrences, from two characters to eight.

/** The kinds of run Explore counts, by their length. */
export const NGRAM_KINDS = { pair: 2, trigram: 3 }

// What the interface calls each kind and its counts.
const WORDS = {
  pair: { name: () => t('explore.pairs'), none: () => t('explore.pairs.none'), failed: () => t('explore.pairs.failed') },
  trigram: { name: () => t('explore.trigrams'), none: () => t('explore.trigrams.none'), failed: () => t('explore.trigrams.failed') },
}
export const ngramWords = kind => WORDS[kind]

/** A run's order: by how its letterforms were made (the default, shaped by hand first) or by work. */
export const sortParam = value => value === 'source' ? 'source' : ''

/** The address of a run's page, within a work, a written form, a group and an order when they are chosen. */
export function runAddress(text, { work = '', form = '', hand = '', sort = '' } = {}) {
  const query = new URLSearchParams(Object.entries({ work, form, hand: handParam(hand), sort: sortParam(sort) }).filter(([, value]) => value))
  return '/sequence/' + encodeURIComponent(text) + (query.size ? '?' + query : '')
}

/** The runs of a kind, most frequent first: `{ items: [{ text, n }], limit }`. */
export function ngramCounts(kind, work = '', options = {}) {
  return request(`/atlas/ngrams/${NGRAM_KINDS[kind]}` + (work ? '?' + new URLSearchParams({ document: work }) : ''), undefined, options)
}

/** One page of a run's occurrences: `{ text, size, next_offset, items: [{ crops }] }`, and on the first
 *  page `total`, `more` (the count stopped at `total`), `vertical`, the crops of each group
 *  (`hands`), the run's `works` (`{ id, title, count }`) and, for a run of two or three, the written
 *  forms its graphemes gather (`forms`, `[{ text, n }]`), which `form` narrows it to. Without a `limit`
 *  the page is as long as the run allows. */
export function runOccurrences(text, { work = '', form = '', hand = '', sort = '', offset = 0, limit } = {}, options = {}) {
  const query = new URLSearchParams({ text, offset: String(offset), ...(limit ? { limit: String(limit) } : {}), ...(work ? { document: work } : {}),
    ...(form ? { form } : {}),
    ...(handParam(hand) ? { hand } : {}), ...(sortParam(sort) ? { sort } : {}) })
  return request('/atlas/runs?' + query, undefined, options)
}

/** The runs near one: `{ lead, inside: [text], longer: [{ text, n }], siblings: [{ text, n }] }`. */
export function runRelated(text, options = {}) {
  return request('/atlas/runs/related?' + new URLSearchParams({ text }), undefined, options)
}
