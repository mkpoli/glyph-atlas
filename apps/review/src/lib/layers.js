import { t } from './i18n.svelte.js'

/** The character layer's API: candidate search, exact characters, graphemes and ligatures. */
async function get(path, params = {}, { fetch: send = fetch, ...options } = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const response = await send(path + (query.size ? '?' + query : ''), {
    headers: { accept: 'application/json' }, priority: 'high',
    signal: AbortSignal.timeout(15000), ...options,
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : t('layers.readError'))
    error.status = response.status
    throw error
  }
  return value
}

/** The candidate list the search box shows: characters, with their own counts, for a typed query. */
export const suggest = (q, limit = 8, signal) => q ? get('/layers/suggest', { q, limit }, { signal, priority: 'high' }) : Promise.resolve(null)
export const search = (q, expand = 'none') => get('/layers/search', { q, expand })
export const character = (codePoint, expand = 'none') => get('/layers/characters/' + encodeURIComponent(codePoint), { expand })
export const occurrences = (codePoint, params = {}) => get('/layers/occurrences', { code_point: codePoint, ...params })
export const graphemes = (params = {}) => get('/layers/graphemes', params)
export const ligatures = () => get('/layers/ligatures')
export const gallery = (limit = 24, seed = 0, options) => get('/layers/gallery', { limit, seed }, options)
export const candidates = (codePoint, limit = 24, offset = 0, options = {}) => get('/layers/candidates', { code_point: codePoint, limit, offset: offset || '', ...options })
export const layerSummary = () => get('/layers/summary')

/** `2 crops · 212 line matches` — the two evidence kinds, never added into one number. */
export function countsLabel(counts) {
  if (!counts) return ''
  if (counts.requires_family_scope) return counts.family_glyphs != null
    ? t('layers.familySamples.count', { count: counts.family_glyphs }) : t('layers.familySamples')
  const parts = []
  if (counts.glyphs != null) parts.push(t('layers.crops.count', { count: counts.glyphs }))
  if (counts.lines) parts.push(t('layers.lineMatches.count', { count: counts.lines }))
  if (counts.pages) parts.push(t('layers.pageMatches.count', { count: counts.pages }))
  if (!parts.length && counts.imported) parts.push(t('layers.inCollection.count', { count: counts.imported }))
  return parts.join(' · ')
}

/** What a row's own collection holds, when the corpus knows nothing about the character. */
export function ownLabel(row) {
  return row?.occurrence_count ? t('layers.inCollection.count', { count: row.occurrence_count }) : t('layers.noOccurrenceYet')
}
