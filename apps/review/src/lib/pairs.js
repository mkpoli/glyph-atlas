import { request } from './client.js'

// A two-character pair's page: the occurrences of the text two crops on a line make, in one work or all.

/** The address of a pair's page, within a work when one is chosen. */
export function pairAddress(text, work = '') {
  return '/pair/' + encodeURIComponent(text) + (work ? '?' + new URLSearchParams({ work }) : '')
}

/** One page of a pair's occurrences: `{ text, total, next_offset, items: [{ first, second }] }`. */
export function pairOccurrences(text, { work = '', offset = 0, limit = 48 } = {}, options = {}) {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit), ...(work ? { document: work } : {}) })
  return request('/atlas/pairs/' + encodeURIComponent(text) + '?' + query, undefined, options)
}
