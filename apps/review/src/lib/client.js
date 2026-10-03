import { t } from './i18n.svelte.js'
import { isBusy, retryAfter, waitOut } from './busy.svelte.js'
import { deliver, outbox, useSender } from './outbox.svelte.js'

// Whoever keeps records read ahead hears of every write, which may have changed any of them.
const writeListeners = new Set()
export const afterWrite = listener => { writeListeners.add(listener); return () => writeListeners.delete(listener) }

// `options.fetch` is the fetch a page's load function was given, which answers API paths on the
// server as well as in the browser. `wait: false` reports a busy database at once, for a read made
// ahead of the reader.
export async function request(path, body, { fetch: send = fetch, wait = true, ...options } = {}) {
  if (body !== undefined) return typeof window === 'undefined' ? settle(await posted(path, body, send, options)) : save(path, body, send, options)
  const answer = async () => { const response = await send(path, options); return { response, value: await response.json() } }
  let answered = await answer()
  // A read the database is too busy for is asked again in the browser, and the page keeps what it
  // shows meanwhile. A page rendered on the server does not wait: its view reads again itself.
  if (wait && typeof window !== 'undefined' && isBusy(answered.response, answered.value)) answered = await waitOut(answer, answered, options.signal)
  return settle(answered)
}

/** The value of an answer, or the error a view shows for it. */
function settle({ response, value }) {
  if (response.ok) return value
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

async function posted(path, body, send, options) {
  const response = await send(path, { ...options, method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  return { response, value: await response.json() }
}

/**
 * One write, made by the signed-in user: a browser without a session starts an anonymous one first,
 * and one whose session has lapsed starts another and sends the write again. `user` is the account
 * a kept save was made by; it is sent only while that account is signed in.
 */
async function write(path, body, { send = fetch, options = {}, user = null } = {}) {
  const { ensureSignedIn, sessionStarted } = await import('./session.svelte.js')
  await sessionStarted()
  const post = () => posted(path, body, send, options)
  const me = await ensureSignedIn()
  if (user && me && me.id !== user) return { response: { ok: false, status: 409 }, value: { detail: t('client.keptForAnother') } }
  let answered = await post()
  for (const listener of writeListeners) listener()
  if (answered.response.status === 401) { await ensureSignedIn({ again: true }); answered = await post() }
  return { ...answered, user: me?.id ?? null }
}
useSender({ send: entry => write(entry.path, entry.body, { user: entry.user }), settle })

// A save the site cannot take yet (the database is busy, the browser is offline) is kept on this
// device and sent in order when it can be; a save made while others wait joins the end of the line,
// so none overtakes one made before it.
async function save(path, body, send, options) {
  if (outbox.pending) return deliver(path, body, (await signedIn())?.id)
  let answered
  try { answered = await write(path, body, { send, options }) }
  catch (error) { if (error instanceof TypeError) return deliver(path, body, (await signedIn())?.id, 0); throw error }
  if (isBusy(answered.response, answered.value)) return deliver(path, body, answered.user, retryAfter(answered.response))
  return settle(answered)
}
const signedIn = async () => { try { return await (await import('./session.svelte.js')).ensureSignedIn() } catch { return null } }
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
