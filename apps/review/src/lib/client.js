export async function request(path, body, options = {}) {
  const response = await fetch(path, { ...options, ...(body === undefined ? {} : {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  }) })
  const value = await response.json()
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : response.status === 409
      ? 'This character changed. Reload to review the current version.' : 'The review could not be saved.')
    error.status = response.status
    throw error
  }
  return value
}
// `purpose` says what the collection is being read for: `browse` is the gallery and keeps every
// record, including the ones a review round withholds; `review` asks for the records a round may
// put in front of a reviewer. It is sent on every call rather than defaulted by the server, so a
// caller that wants round items says so.
export const catalogue = ({ purpose = 'browse', ...params } = {}, options = {}) =>
  request('/atlas?' + new URLSearchParams(Object.entries({ purpose, ...params }).filter(([, v]) => v !== '' && v != null)), undefined, options)
export const character = id => request('/atlas/characters/' + encodeURIComponent(id))
export const corpusCharacter = id => request('/atlas/corpus/character?' + new URLSearchParams({ id }))
export const randomSeed = () => Math.floor(Math.random() * 2147483647)
export const number = n => Number(n ?? 0).toLocaleString()
export function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}
export function remember(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* Server records remain available. */ }
}
export function reviewer() {
  const key = 'atlas.reviewer'
  let id = stored(key, null)
  if (!id) { id = 'reviewer-' + crypto.randomUUID().slice(0, 8); remember(key, id) }
  return id
}
export function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const suggestionCache = new Map()
export function suggestionsFor(item, source = 'visual') {
  const key = source + ':' + item.id + ':' + item.revision + ':' + item.image_sha256
  if (!suggestionCache.has(key)) {
    if (suggestionCache.size > 256) suggestionCache.delete(suggestionCache.keys().next().value)
    const query = new URLSearchParams({ revision: item.revision, image_sha256: item.image_sha256 })
    const suffix = source === 'context' ? '/context' : ''
    const promise = request('/atlas/characters/' + encodeURIComponent(item.id) + '/suggestions' + suffix + '?' + query,
      undefined, { signal: AbortSignal.timeout(source === 'context' ? 8000 : 30000) })
      .catch(() => ({ status: 'unavailable', candidates: [] }))
      .then(result => {
        // Context changes with neighboring edits. Share in-flight calls only; failures can retry.
        if (source === 'context' || result.status !== 'ready') {
          if (suggestionCache.get(key) === promise) suggestionCache.delete(key)
        }
        return result
      })
    suggestionCache.set(key, promise)
  }
  return suggestionCache.get(key)
}
