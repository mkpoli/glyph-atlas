import { request } from './client.js'
import { t } from './i18n.svelte.js'

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

/** The address of a run's page, within a work when one is chosen. */
export function runAddress(text, work = '') {
  return '/run/' + encodeURIComponent(text) + (work ? '?' + new URLSearchParams({ work }) : '')
}

/** The runs of a kind, most frequent first: `{ items: [{ text, n }], limit }`. */
export function ngramCounts(kind, work = '', options = {}) {
  return request(`/atlas/ngrams/${NGRAM_KINDS[kind]}` + (work ? '?' + new URLSearchParams({ document: work }) : ''), undefined, options)
}

/** One page of a run's occurrences: `{ text, total, more, next_offset, items: [{ crops }] }`, where `more`
 *  says the count stopped at `total`. */
export function runOccurrences(text, { work = '', offset = 0, limit = 48 } = {}, options = {}) {
  const query = new URLSearchParams({ text, offset: String(offset), limit: String(limit), ...(work ? { document: work } : {}) })
  return request('/atlas/runs?' + query, undefined, options)
}
