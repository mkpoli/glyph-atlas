// Saves the site could not take yet, kept on this device and sent in order once it can: the database
// was busy (an import holds it), the browser was offline, or the site was rate-limiting. Each save
// names the submission id it was made with, and the server answers a repeat of one with its first
// result, so sending one twice records it once. `DatabaseStatus` shows what is waiting.
import { backoff, isBusy, retryAfter } from './busy.svelte.js'

export const outbox = $state({ pending: 0, refused: [] })

// The browser stores saves in IndexedDB; where that cannot open (some private windows), in
// localStorage; failing both, in this page only.
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
const memory = new Map()
const fallback = {
  all() { try { return JSON.parse(localStorage.getItem(FALLBACK)) ?? [] } catch { return [...memory.values()] } },
  write(entries) { try { localStorage.setItem(FALLBACK, JSON.stringify(entries)) } catch { memory.clear(); for (const e of entries) memory.set(e.key, e) } },
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
  return (Array.isArray(found) ? found : fallback.all()).sort((a, b) => a.seq - b.seq)
}
async function put(entry) {
  if (!(await transaction('readwrite', store => store.put(entry)))) fallback.write([...fallback.all().filter(e => e.key !== entry.key), entry])
}
async function remove(key) {
  if (!(await transaction('readwrite', store => store.delete(key)))) fallback.write(fallback.all().filter(e => e.key !== key))
}
const count = async () => { outbox.pending = (await entries()).length }

// `send(entry)` makes one save as the signed-in user and answers `{ response, value }`; `settle`
// turns an answer into the saved value or the error a view shows. Both come from `client.js`.
let sender = null
export function useSender(value) { sender = value }

// The page's own callers wait for their save; one kept from an earlier page has nobody waiting.
const waiting = new Map()
let sequence = 0
const keyOf = (path, body) => path + '#' + (typeof body?.id === 'string' ? body.id : path.endsWith('/undo') ? '' : crypto.randomUUID())

/**
 * Keep a save and send it when it can be; resolves with what the site answers. `wait` (seconds) is
 * how long the site asked for before the first try; without it the save joins the line at once.
 */
export async function deliver(path, body, user, wait = null) {
  const entry = { key: keyOf(path, body), path, body, user: user ?? null, seq: Date.now() * 1000 + (sequence++ % 1000) }
  await put(entry)
  const answered = new Promise((resolve, reject) => waiting.set(entry.key, { resolve, reject, entry }))
  await count()
  if (wait === null) drain()
  else if (!draining) again(wait)
  return answered
}

/** Whether an answer means "not now": the site will take the same save later. */
export const later = ({ response, value }) => isBusy(response, value) || response.status === 429 || response.status === 502 || response.status === 504

let draining = null, timer = null, failures = 0
/** Send what is waiting, oldest first, stopping at the first save the site cannot take yet. */
export function drain() {
  clearTimeout(timer); timer = null
  draining ??= run().finally(() => { draining = null })
  return draining
}
function again(wait = 0) {
  clearTimeout(timer)
  timer = setTimeout(drain, backoff(failures++, wait))
}
async function run() {
  if (!sender) return
  // One tab sends at a time, so two tabs do not send the same save together.
  const exclusive = typeof navigator !== 'undefined' && navigator.locks ? work => navigator.locks.request('atlas-outbox', work) : work => work()
  await exclusive(async () => {
    const kept = await entries()
    // A save of this page's that another tab sent meanwhile is asked once more: the site answers a
    // repeat with the first result, which is what the view here is waiting for.
    const keys = new Set(kept.map(entry => entry.key))
    for (const [key, { entry }] of waiting) if (!keys.has(key)) kept.unshift(entry)
    for (const entry of kept) {
      let answer
      try { answer = await sender.send(entry) } catch { again(); return }
      if (later(answer)) { again(retryAfter(answer.response)); return }
      failures = 0
      await remove(entry.key)
      const waiter = waiting.get(entry.key)
      waiting.delete(entry.key)
      let value
      try { value = sender.settle(answer) } catch (error) {
        // A refusal (the crop changed since, say) goes to the view that made the save, which shows it
        // as it shows any refusal; a save from an earlier page is shown in the corner instead.
        if (waiter) waiter.reject(error)
        else outbox.refused.push({ key: entry.key, message: error.message })
        continue
      }
      waiter?.resolve(value)
    }
  })
  await count()
}

/** On page load: count what an earlier page left, send it, and send again when the browser is back online. */
export function resume() {
  count().then(() => { if (outbox.pending) drain() })
  const online = () => drain()
  const visible = () => { if (document.visibilityState === 'visible' && outbox.pending) drain() }
  addEventListener('online', online)
  document.addEventListener('visibilitychange', visible)
  return () => { removeEventListener('online', online); document.removeEventListener('visibilitychange', visible); clearTimeout(timer) }
}

export const dismiss = key => { outbox.refused = outbox.refused.filter(r => r.key !== key) }
