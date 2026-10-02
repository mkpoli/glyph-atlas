import { request } from './client.js'
import { t } from './i18n.svelte.js'

// Pairs and trigrams: runs of two or three crops that follow each other on a line, counted by the text
// their labels make, and the page of one run's occurrences, in one work or all.

/** The kinds of run, by the length the site counts them at. */
export const NGRAM_KINDS = { pair: 2, trigram: 3 }

// What the interface calls each kind, its counts and its pages.
const WORDS = {
  pair: { name: () => t('explore.pairs'), none: () => t('explore.pairs.none'), failed: () => t('explore.pairs.failed'), empty: () => t('pair.empty') },
  trigram: { name: () => t('explore.trigrams'), none: () => t('explore.trigrams.none'), failed: () => t('explore.trigrams.failed'), empty: () => t('trigram.empty') },
}
export const ngramWords = kind => WORDS[kind]

/** The address of a run's page, within a work when one is chosen. */
export function ngramAddress(kind, text, work = '') {
  return `/${kind}/` + encodeURIComponent(text) + (work ? '?' + new URLSearchParams({ work }) : '')
}

/** The runs of a kind, most frequent first: `{ items: [{ text, n }], limit }`. */
export function ngramCounts(kind, work = '', options = {}) {
  return request(`/atlas/ngrams/${NGRAM_KINDS[kind]}` + (work ? '?' + new URLSearchParams({ document: work }) : ''), undefined, options)
}

/** One page of a run's occurrences: `{ text, total, next_offset, items: [{ crops }] }`. */
export function ngramOccurrences(kind, text, { work = '', offset = 0, limit = 48 } = {}, options = {}) {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit), ...(work ? { document: work } : {}) })
  return request(`/atlas/ngrams/${NGRAM_KINDS[kind]}/` + encodeURIComponent(text) + '?' + query, undefined, options)
}
