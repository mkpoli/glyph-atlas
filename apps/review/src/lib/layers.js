/** The character layer's API: candidate search, exact characters, graphemes and ligatures. */
async function get(path, params = {}, options = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const response = await fetch(path + (query.size ? '?' + query : ''), {
    headers: { accept: 'application/json' }, priority: 'high',
    signal: AbortSignal.timeout(15000), ...options,
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : 'The character layer could not be read.')
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
export const gallery = (limit = 24, seed = 0) => get('/layers/gallery', { limit, seed })
export const candidates = (codePoint, limit = 24, offset = 0, options = {}) => get('/layers/candidates', { code_point: codePoint, limit, offset: offset || '', ...options })
export const layerSummary = () => get('/layers/summary')

/** `2 crops · 212 line matches` — the two evidence kinds, never added into one number. */
export function countsLabel(counts) {
  if (!counts) return ''
  if (counts.requires_family_scope) return counts.family_glyphs != null ? `${counts.family_glyphs} family samples` : 'Family samples'
  const parts = []
  if (counts.glyphs != null) parts.push(`${counts.glyphs} ${counts.glyphs === 1 ? 'crop' : 'crops'}`)
  if (counts.lines) parts.push(`${counts.lines} line ${counts.lines === 1 ? 'match' : 'matches'}`)
  if (counts.pages) parts.push(`${counts.pages} page ${counts.pages === 1 ? 'match' : 'matches'}`)
  if (!parts.length && counts.imported) parts.push(`${counts.imported} in this collection`)
  return parts.join(' · ')
}

/** What a row's own collection holds, when the corpus knows nothing about the character. */
export function ownLabel(row) {
  return row?.occurrence_count ? `${row.occurrence_count} in this collection` : 'no occurrence yet'
}
