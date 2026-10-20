// Saves the site could not take yet, kept on this device and sent in order once it can: the database
// was busy (an import holds it), the browser was offline, or the site was rate-limiting. Only a save
// that names its submission id is kept, since the server answers a repeat of one with its first
// result and so records it once; an undo is named by its path, and a repeat of one changes nothing.
// While the site pauses writes, which can last hours, nobody waits on a kept save: each caller is told
// at once that it is kept (an error with `kept: true`), and it is sent once writes resume.
// `DatabaseStatus` shows what is waiting.
import { backoff, isBusy, isPaused, retryAfter } from './busy.svelte.js'

// `pending` counts this reader's kept saves; `keys` names every save kept on this device, whoever made
// it; `lost` names every kept save the site refused, in this tab or another, which stays named after
// the corner's note is dismissed.
export const outbox = $state({ pending: 0, refused: [], paused: false, keys: [], lost: [] })

// The browser stores saves in IndexedDB; a save IndexedDB would not take goes to localStorage, and
// failing both, to this page's memory. All three are read together.
const DATABASE = 'atlas-outbox', STORE = 'saves', FALLBACK = 'atlas.outbox'
let opened = null
function database() {
  opened ??= new Promise((resolve, reject) => {
    const asked = indexedDB.open(DATABASE, 1)
    asked.onupgradeneeded = () => asked.result.createObjectStore(STORE, { keyPath: 'key' })
    asked.onsuccess = () => resolve(asked.result)
    asked.onerror = () => reject(asked.error)
  }).catch(() => null)
  return opened
}
// The keys of kept saves the site refused, shared with the other tabs: the last few hundred.
const LOST = 'atlas.outbox.lost', LOST_KEPT = 256
const storedLost = () => { try { const keys = JSON.parse(localStorage.getItem(LOST)); return Array.isArray(keys) ? keys : [] } catch { return [] } }
function refuse(key) {
  outbox.lost.push(key)
  try { localStorage.setItem(LOST, JSON.stringify([...new Set([...storedLost(), key])].slice(-LOST_KEPT))) } catch { /* This tab still knows. */ }
}
const memory = new Map()
const fallback = {
  all() { let stored = []; try { stored = JSON.parse(localStorage.getItem(FALLBACK)) ?? [] } catch { /* None stored. */ } return [...stored, ...memory.values()] },
  write(entries) {
    try { localStorage.setItem(FALLBACK, JSON.stringify(entries)); memory.clear() }
    catch { memory.clear(); for (const e of entries) memory.set(e.key, e) }
  },
}
function transaction(mode, use) {
  return database().then(db => db && new Promise((resolve, reject) => {
    const run = db.transaction(STORE, mode), result = use(run.objectStore(STORE))
    run.oncomplete = () => resolve(result.result ?? true)
    run.onerror = run.onabort = () => reject(run.error)
  })).catch(() => null)
}
async function entries() {
  const found = await transaction('readonly', store => store.getAll())
  const all = new Map([...(Array.isArray(found) ? found : []), ...fallback.all()].map(entry => [entry.key, entry]))
  return [...all.values()].sort((a, b) => a.seq - b.seq)
}
async function put(entry) {
  if (!(await transaction('readwrite', store => store.put(entry)))) fallback.write([...fallback.all().filter(e => e.key !== entry.key), entry])
}
async function remove(key) {
  await transaction('readwrite', store => store.delete(key))
  const rest = fallback.all()
  if (rest.some(e => e.key === key)) fallback.write(rest.filter(e => e.key !== key))
}
// The saves this reader can send now. One made by an account that is not signed in waits for it, and
// is not counted meanwhile.
const mine = async () => (await entries()).filter(entry => sender?.mine(entry) !== false)
// What the corner counts, what is stored, and what another tab saw refused.
const count = async () => {
  const all = await entries()
  outbox.pending = all.filter(entry => sender?.mine(entry) !== false).length
  outbox.keys = all.map(entry => entry.key)
  const lost = storedLost().filter(key => !outbox.lost.includes(key))
  if (lost.length) outbox.lost.push(...lost)
}

/**
 * Where a kept save stands, by its key: `kept` while it is stored (also while it waits for an account
 * that is not signed in), `refused` once the site refused it, else `sent`.
 */
export const keptState = key => outbox.lost.includes(key) ? 'refused' : outbox.keys.includes(key) ? 'kept' : 'sent'

/**
 * The crops this reader's kept saves decide, by id: the crops of a kept round (flagged, seen or skipped)
 * and of a kept correction. They still read as pending until the saves are sent, so a round dealt
 * meanwhile leaves them out. A save whose undo is kept too decides nothing, and one another account
 * made leaves its crops to this reader.
 */
export async function keptCrops() {
  const kept = await mine()
  const undone = new Set(kept.map(entry => /\/([^/]+)\/undo$/.exec(entry.path)?.[1]).filter(Boolean))
  const crops = new Set()
  for (const { body } of kept) {
    if (undone.has(body?.id)) continue
    for (const list of [body?.answers, body?.seen, body?.skipped, body?.crops])
      for (const crop of Array.isArray(list) ? list : []) if (crop?.id != null) crops.add(crop.id)
  }
  return crops
}

/** Whether any save is kept, in this tab or another: a new save then joins the end of the line. */
export const anyKept = async () => outbox.pending > 0 || (await mine()).length > 0

// `send(entry)` makes one save and answers `{ response, value }`, or `{ wait: true, retryAfter, paused }`
// when it cannot be sent yet (`retryAfter` in seconds, 0 for no particular wait), or `{ skip: true }`
// when it belongs to an account that is not signed in. `settle` turns an answer into the saved value
// or the error a view shows; `mine(entry)` says whether the signed-in reader may send it; `kept()` is
// the error a caller gets for a save kept while writes are paused. All four come from `client.js`.
let sender = null
export function useSender(value) { sender = value }

/** Whether a save can be kept: it names its submission, so sending it twice records it once. */
export const keepable = (path, body) => typeof body?.id === 'string' || /\/undo$/.test(path)
/** The key a save is kept under. */
export const keyOf = (path, body) => path + '#' + (typeof body?.id === 'string' ? body.id : '')

// The page's own callers wait for their save; one kept from an earlier page has nobody waiting.
const waiting = new Map()
let sequence = 0

/**
 * Keep a save and send it when it can be; resolves with what the site answers. `wait` (seconds) is
 * how long the site asked for before the first try; without it the save joins the line at once.
 */
export async function deliver(path, body, user, wait = null) {
  const entry = { key: keyOf(path, body), path, body, user: user?.id ?? null, anonymous: Boolean(user?.anonymous),
    seq: Date.now() * 1000 + (sequence++ % 1000) }
  await put(entry)
  const answered = new Promise((resolve, reject) => {
    waiting.set(entry.key, [...(waiting.get(entry.key) ?? []), { resolve, reject, entry }])
  })
  await count()
  if (wait === null) drain()
  else again(wait)
  return answered
}

/**
 * Keep a save while writes are paused, with nobody waiting on it: it is sent after `wait` seconds, or
 * once writes resume, and a refusal then is shown in the corner.
 */
export async function keep(path, body, user, wait = 0) {
  await put({ key: keyOf(path, body), path, body, user: user?.id ?? null, anonymous: Boolean(user?.anonymous),
    seq: Date.now() * 1000 + (sequence++ % 1000) })
  outbox.paused = true
  await count()
  // A pass running now may have read the line before this save joined it, and goes round again.
  if (draining) dirty = true
  else again(wait)
}

/** Whether an answer means "not now": the site will take the same save later. */
const later = ({ response, value }) => isBusy(response, value) || isPaused(response, value) || response.status === 429 || response.status === 502 || response.status === 504
/** Whether a send failed for want of a network, which only time or reconnecting mends. */
export const offline = error => error instanceof TypeError && /fetch|network|load failed/i.test(error.message)

// A save whose sending keeps failing for another reason is given up after this many tries, and the
// failure is shown, so one broken save cannot hold up every save after it.
const TRIES = 5
const failed = new Map()
let draining = null, dirty = false, timer = null, failures = 0
/** Send what is waiting, oldest first, stopping at the first save the site cannot take yet. */
export function drain() {
  if (draining) { dirty = true; return draining }
  clearTimeout(timer); timer = null
  draining = (async () => {
    // A save kept while a pass ran is sent by another pass.
    do { dirty = false; await run() } while (dirty && !timer)
  })().finally(() => { draining = null })
  return draining
}
function again(wait = 0) {
  clearTimeout(timer)
  timer = setTimeout(() => { timer = null; drain() }, backoff(failures++, wait))
}
function settleWaiters(key, use) {
  const list = waiting.get(key) ?? []
  waiting.delete(key)
  for (const waiter of list) use(waiter)
  return list.length > 0
}
async function run() {
  if (!sender) return
  // One tab sends at a time, so two tabs do not send the same save together.
  const exclusive = typeof navigator !== 'undefined' && navigator.locks ? work => navigator.locks.request('atlas-outbox', work) : work => work()
  await exclusive(async () => {
    const kept = await entries()
    // A save of this page's that another tab sent meanwhile is asked once more: the site answers a
    // repeat of a submission with its first result, which is what the view here is waiting for.
    const keys = new Set(kept.map(entry => entry.key))
    for (const [key, [{ entry }]] of waiting) if (!keys.has(key)) kept.unshift(entry)
    for (const entry of kept) {
      let answer
      try { answer = await sender.send(entry) } catch (error) {
        if (offline(error)) { again(); return }
        const tries = (failed.get(entry.key) ?? 0) + 1
        if (tries < TRIES) { failed.set(entry.key, tries); again(); return }
        failed.delete(entry.key)
        await remove(entry.key)
        refuse(entry.key)
        if (!settleWaiters(entry.key, waiter => waiter.reject(error))) outbox.refused.push({ key: entry.key, message: error.message })
        continue
      }
      if (answer.skip) continue
      if (answer.wait || later(answer)) {
        // Paused, the callers of this page's kept saves are told they are kept, and stop waiting.
        outbox.paused = Boolean(answer.paused || (answer.response && isPaused(answer.response, answer.value)))
        if (outbox.paused) for (const key of [...waiting.keys()]) settleWaiters(key, waiter => waiter.reject(sender.kept()))
        again(answer.response ? retryAfter(answer.response) : answer.retryAfter ?? 0); return
      }
      outbox.paused = false
      failures = 0
      failed.delete(entry.key)
      await remove(entry.key)
      let value
      try { value = sender.settle(answer) } catch (error) {
        // A refusal (the crop changed since, say) goes to the view that made the save, which shows it
        // as it shows any refusal; a save from an earlier page is shown in the corner instead.
        refuse(entry.key)
        if (!settleWaiters(entry.key, waiter => waiter.reject(error))) outbox.refused.push({ key: entry.key, message: error.message })
        continue
      }
      settleWaiters(entry.key, waiter => waiter.resolve(value))
    }
  })
  await count()
}

/**
 * On page load: count what an earlier page left, send it, and send again when the browser is back
 * online. What another tab sent or saw refused is read again when this one is shown or focused, or
 * when that tab writes the list it shares.
 */
export function resume() {
  count().then(() => { if (outbox.pending) drain() })
  const online = () => drain()
  const visible = () => { if (document.visibilityState === 'visible') count().then(() => { if (outbox.pending) drain() }) }
  const shared = event => { if (event.key === LOST || event.key === FALLBACK) count() }
  addEventListener('online', online)
  addEventListener('focus', visible)
  addEventListener('storage', shared)
  document.addEventListener('visibilitychange', visible)
  return () => {
    removeEventListener('online', online); removeEventListener('focus', visible); removeEventListener('storage', shared)
    document.removeEventListener('visibilitychange', visible); clearTimeout(timer)
  }
}

export const dismiss = key => { outbox.refused = outbox.refused.filter(r => r.key !== key) }
