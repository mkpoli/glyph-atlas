import { t } from './i18n.svelte.js'
import { isBusy, waitOut } from './busy.svelte.js'

// Whoever keeps records read ahead hears of every write, which may have changed any of them.
const writeListeners = new Set()
export const afterWrite = listener => { writeListeners.add(listener); return () => writeListeners.delete(listener) }

// `options.fetch` is the fetch a page's load function was given, which answers API paths on the
// server as well as in the browser.
export async function request(path, body, { fetch: send = fetch, ...options } = {}) {
  const post = () => send(path, { ...options, ...(body === undefined ? {} : {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  }) })
  // A write is made by the signed-in user; a browser without a session starts an anonymous one first,
  // and one whose session has lapsed starts another and sends the write again.
  const writes = body !== undefined && typeof window !== 'undefined'
  const { ensureSignedIn } = writes ? await import('./session.svelte.js') : {}
  if (writes) await ensureSignedIn()
  const answer = async () => { const response = await post(); return { response, value: await response.json() } }
  let { response, value } = await answer()
  if (writes) for (const listener of writeListeners) listener()
  if (writes && response.status === 401) { await ensureSignedIn({ again: true }); ({ response, value } = await answer()) }
  // A read the database is too busy for is asked again in the browser, and the page keeps what it
  // shows meanwhile. A page rendered on the server does not wait: its view reads again itself.
  if (!writes && typeof window !== 'undefined' && isBusy(response, value)) ({ response, value } = await waitOut(answer, { response, value }, options.signal))
  if (!response.ok) {
    // FastAPI reports a validation failure as a list of problems; show their messages.
    const listed = Array.isArray(value.detail) ? value.detail.map(problem => problem.msg).filter(Boolean).join('; ') : ''
    const error = new Error(isBusy(response, value) ? t('client.busy') : typeof value.detail === 'string' ? value.detail : listed ? listed
      : response.status === 409 ? t('client.changed') : t('client.saveFailed'))
    error.code = value.code
    error.status = response.status
    // A crop a publication retired names the crop that replaced it.
    if (typeof value.replaced_by === 'string') error.replacedBy = value.replaced_by
    // A batch the site refused names the crops it refused.
    if (Array.isArray(value.targets)) error.targets = value.targets
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
export const character = (id, options) => request('/atlas/characters/' + encodeURIComponent(id), undefined, options)
export const similar = (id, options) => request('/atlas/characters/' + encodeURIComponent(id) + '/similar', undefined, options)
export const history = (params = {}, options = {}) =>
  request('/atlas/history?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)), undefined, options)
export const corpusCharacter = (id, options) => request('/atlas/corpus/character?' + new URLSearchParams({ id }), undefined, options)
export const randomSeed = () => Math.floor(Math.random() * 2147483647)
export { formatNumber as number, formatSerial } from './i18n.svelte.js'
export function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}
export function remember(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* Server records remain available. */ }
}
export function forget(key) {
  try { localStorage.removeItem(key) } catch { /* Nothing was stored. */ }
}
export function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const suggestionCache = new Map()
export function suggestionsFor(item, source = 'visual') {
  // A corpus glyph names its pixels by its source revision, a crop by its page hash, and a crop
  // published without a hash by its image.
  const pixels = item.origin === 'corpus' ? { source_revision: item.source_revision }
    : item.image_sha256 ? { image_sha256: item.image_sha256 } : { image: item.image }
  const key = source + ':' + item.id + ':' + item.revision + ':' + Object.values(pixels)[0]
  if (!suggestionCache.has(key)) {
    if (suggestionCache.size > 256) suggestionCache.delete(suggestionCache.keys().next().value)
    const query = new URLSearchParams({ revision: item.revision, ...pixels })
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
