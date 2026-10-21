import { t } from './i18n.svelte.js'
import { isReload } from './busy.svelte.js'

/** The character layer's API: candidate search, exact characters, graphemes and ligatures. */
async function get(path, params = {}, { fetch: send = fetch, ...options } = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const response = await send(path + (query.size ? '?' + query : ''), {
    headers: { accept: 'application/json' }, priority: 'high',
    signal: AbortSignal.timeout(15000), ...options,
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) {
    // A page this tab asked for in a way the site no longer answers asks for a reload.
    const error = new Error(isReload(response, value) ? t('client.reload') : typeof value.detail === 'string' ? value.detail : t('layers.readError'))
    error.status = response.status
    error.code = value.code
    throw error
  }
  return value
}

/** The candidate list the search box shows: characters, with their own counts, for a typed query. */
export const suggest = (q, limit = 8, signal) => q ? get('/layers/suggest', { q, limit }, { signal, priority: 'high' }) : Promise.resolve(null)
export const search = (q, expand = 'none') => get('/layers/search', { q, expand })
/** What the IDS editor starts from: a character's descriptions and the substitutes of their components. */
export const structure = char => get('/layers/structure', { c: char })
export const character = (codePoint, expand = 'none', options) => get('/layers/characters/' + encodeURIComponent(codePoint), { expand }, options)
export const occurrences = (codePoint, params = {}, options) => get('/layers/occurrences', { code_point: codePoint, ...params }, options)
export const graphemes = (params = {}) => get('/layers/graphemes', params)
export const ligatures = () => get('/layers/ligatures')
/** Copies of corpus glyphs from a seed's point of their shuffle, of one script group or of every one ('all'). */
export const gallery = (limit = 24, seed = 0, group = 'all', options) => get('/layers/gallery', { limit, seed, group: group === 'all' ? '' : group }, options)
/** A page of a character's corpus glyphs, from the cursor the page before named (`next`), or the first. */
export const candidates = (codePoint, limit = 24, after = null, params = {}, options) => get('/layers/candidates', { code_point: codePoint, limit, after, ...params }, options)
export const decades = (codePoint, params = {}, options) => get('/layers/decades', { code_point: codePoint, ...params }, options)
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
