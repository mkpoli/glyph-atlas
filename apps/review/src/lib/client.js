import { t } from './i18n.svelte.js'
import { isBusy, retryAfter, waitOut } from './busy.svelte.js'
import { anyKept, deliver, keepable, offline, useSender } from './outbox.svelte.js'

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
 * and one whose session has lapsed starts another and sends the write again.
 */
async function write(path, body, { send = fetch, options = {} } = {}) {
  const { ensureSignedIn } = await import('./session.svelte.js')
  const post = () => posted(path, body, send, options)
  let me = await ensureSignedIn()
  let answered = await post()
  for (const listener of writeListeners) listener()
  if (answered.response.status === 401) { me = await ensureSignedIn({ again: true }); answered = await post() }
  return { ...answered, user: me ?? null }
}

/**
 * A kept save, sent as the reader it was made by. One made by an account waits until that account is
 * signed in again, and the outbox never starts a session to send it: only a save made anonymously
 * may go out under a new anonymous session, as a direct write would after one lapses.
 */
async function resend(entry) {
  const { ensureSignedIn, sessionStarted, signedInUser } = await import('./session.svelte.js')
  await sessionStarted()
  const me = signedInUser()
  if (!mine(entry, me)) return { skip: true }
  if (!me) { try { await ensureSignedIn() } catch { return { wait: true } } }
  let answered = await posted(entry.path, entry.body, fetch, {})
  for (const listener of writeListeners) listener()
  if (answered.response.status === 401) {
    // An account whose session ended is asked to sign in again; the save waits for it.
    if (!entry.anonymous) { try { await ensureSignedIn({ again: true }) } catch { /* The sign-in form is open. */ } return { wait: true } }
    try { await ensureSignedIn({ again: true }) } catch { return { wait: true } }
    answered = await posted(entry.path, entry.body, fetch, {})
  }
  return answered
}
/** Whether the reader signed in now (or nobody yet) may send a kept save. */
function mine(entry, me) {
  if (!entry.user) return true
  if (!me) return entry.anonymous
  return me.id === entry.user || (entry.anonymous && me.anonymous)
}
useSender({ send: resend, settle, mine: entry => mine(entry, currentUser()) })
let currentUser = () => null
if (typeof window !== 'undefined') import('./session.svelte.js').then(session => { currentUser = session.signedInUser }).catch(() => {})

// A save the site cannot take yet (the database is busy, the browser is offline) is kept on this
// device and sent in order when it can be; a save made while others wait joins the end of the line,
// so none overtakes one made before it. A save that names no submission is never kept: sending it
// twice could record it twice.
async function save(path, body, send, options) {
  const kept = keepable(path, body)
  if (kept && await anyKept()) return deliver(path, body, await reader())
  let answered
  try { answered = await write(path, body, { send, options }) }
  catch (error) { if (kept && offline(error)) return deliver(path, body, await reader(), 0); throw error }
  if (kept && isBusy(answered.response, answered.value)) return deliver(path, body, answered.user, retryAfter(answered.response))
  return settle(answered)
}
/** Who a save is made by, once the page knows; signing in anonymously if nobody is, as a write does. */
async function reader() {
  try {
    const { ensureSignedIn, sessionStarted } = await import('./session.svelte.js')
    await sessionStarted()
    return await ensureSignedIn()
  } catch { return null }
}
// `purpose` says what the collection is being read for: `browse` is the gallery and keeps every
// record, including the ones a review round withholds; `review` asks for the records a round may
// put in front of a reviewer. It is sent on every call rather than defaulted by the server, so a
// caller that wants round items says so.
export const catalogue = ({ purpose = 'browse', ...params } = {}, options = {}) =>
  request('/atlas?' + new URLSearchParams(Object.entries({ purpose, ...params }).filter(([, v]) => v !== '' && v != null)), undefined, options)
export const character = (id, options) => request('/atlas/characters/' + encodeURIComponent(id), undefined, options)
/** A crop's neighbours on its line, four either side in reading order: `{ items: [{ id, offset, label, image, revision, image_sha256, state }] }`, empty when it has none. */
export const line = (id, options) => request('/atlas/characters/' + encodeURIComponent(id) + '/line', undefined, options)
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
