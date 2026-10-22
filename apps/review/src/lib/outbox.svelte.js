// Saves the site could not take yet, kept on this device and sent in order once it can: the database
// was busy (an import holds it), the browser was offline, or the site was rate-limiting. Only a save
// that names its submission id is kept, since the server answers a repeat of one with its first
// result and so records it once; an undo is named by its path, and a repeat of one changes nothing.
// While the site pauses writes, which can last hours, nobody waits on a kept save: each caller is told
// at once that it is kept (an error with `kept: true`), and it is sent once writes resume.
// A save refused as naming a crop by an old id the site no longer holds (409 `reload`) can never be
// taken: it stays on the device marked `gone`, is never sent again, and is shown with what it named
// until the reader discards it. `DatabaseStatus` shows what is waiting.
import { backoff, isBusy, isPaused, isReload, retryAfter } from './busy.svelte.js'

// `pending` counts this reader's kept saves; `keys` names every save kept on this device, whoever made
// it; `lost` names every kept save the site refused, in this tab or another, which stays named after
// the corner's note is dismissed.
export const outbox = $state({ pending: 0, refused: [], paused: false, keys: [], lost: [] })

// The browser stores saves in IndexedDB; a save IndexedDB would not take goes to localStorage, and
// failing both, to this page's memory. All three are read together. A write leaves one copy: one
// IndexedDB takes clears the fallback copy, and one it refuses deletes the IndexedDB copy where it
// can. Where both remain, the fallback copy is the later one.
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
  if (!outbox.lost.includes(key)) outbox.lost.push(key)
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
  // A fallback copy replaces the IndexedDB copy of the same save.
  const all = new Map([...(Array.isArray(found) ? found : []), ...fallback.all()].map(entry => [entry.key, entry]))
  return [...all.values()].sort((a, b) => a.seq - b.seq)
}
async function put(entry) {
  if (await transaction('readwrite', store => store.put(entry))) return dropFallback(entry.key)
  fallback.write([...fallback.all().filter(e => e.key !== entry.key), entry])
  await transaction('readwrite', store => store.delete(entry.key))
}
function dropFallback(key) {
  const rest = fallback.all()
  if (rest.some(e => e.key === key)) fallback.write(rest.filter(e => e.key !== key))
}
/** The kept save under `key` as stored now, or null. */
async function stored(key) {
  const later = fallback.all().find(e => e.key === key)
  if (later) return later
  const found = await transaction('readonly', store => store.get(key))
  return found && typeof found === 'object' ? found : null
}
async function remove(key) {
  await transaction('readwrite', store => store.delete(key))
  dropFallback(key)
}
// Saves the reader discarded on this page: never sent, even by a pass that read them before.
const discarded = new Set()
// A save that may still be sent: neither gone nor discarded.
const live = entry => !entry.gone && !discarded.has(entry.key)
// The saves this reader can send now. One made by an account that is not signed in waits for it, and
// is not counted meanwhile. A gone save is shown, not counted.
const sendable = entry => live(entry) && sender?.mine(entry) !== false
// What the corner counts and shows, what is stored, and what another tab saw refused.
async function count() {
  const all = await entries()
  outbox.pending = all.filter(sendable).length
  outbox.keys = all.map(entry => entry.key)
  const lost = storedLost().filter(key => !outbox.lost.includes(key))
  if (lost.length) outbox.lost.push(...lost)
  for (const entry of all) if (entry.gone && !discarded.has(entry.key) && !outbox.refused.some(r => r.key === entry.key))
    outbox.refused.push({ key: entry.key, gone: true, what: described(entry) })
}
// What a gone save was: the character, form or word it gave, and the crop ids it named.
function described({ path, body }) {
  const said = [body?.character, body?.form, body?.word, body?.grapheme].find(value => typeof value === 'string' && value)
  const segment = /^\/(?:atlas\/characters|layers\/units)\/([^/]+)/.exec(path)?.[1]
  let crops = Array.isArray(body?.crops) ? body.crops.map(crop => crop?.id).filter(id => typeof id === 'string') : []
  if (typeof body?.identity === 'string') crops = [body.identity]
  if (segment) { try { crops = [decodeURIComponent(segment)] } catch { crops = [segment] } }
  const named = crops.slice(0, 3).join(', ') + (crops.length > 3 ? ', …' : '')
  return [said, named].filter(Boolean).join(' · ')
}

/**
 * Where a kept save stands, by its key: `kept` while it is stored (also while it waits for an account
 * that is not signed in), `refused` once the site refused it or found it gone, else `sent`.
 */
export const keptState = key => outbox.lost.includes(key) ? 'refused' : outbox.keys.includes(key) ? 'kept' : 'sent'

/**
 * The crops this reader's kept saves decide, by id: the crops of a kept round (flagged, seen or skipped)
 * and of a kept correction. They still read as pending until the saves are sent, so a round dealt
 * meanwhile leaves them out. A save whose undo is kept too decides nothing, a gone save decides
 * nothing, and one another account made leaves its crops to this reader. Until the page knows who
 * reads, every kept save's crops are left out.
 */
export async function keptCrops() {
  const unknown = sender && !sender.known()
  const kept = (await entries()).filter(entry => unknown ? live(entry) : sendable(entry))
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
export const anyKept = async () => outbox.pending > 0 || (await entries()).some(sendable)

// `send(entry)` makes one save and answers `{ response, value }`, or `{ wait: true, retryAfter, paused }`
// when it cannot be sent yet (`retryAfter` in seconds, 0 for no particular wait), or `{ skip: true }`
// when it belongs to an account that is not signed in. `settle` turns an answer into the saved value
// or the error a view shows; `mine(entry)` says whether the signed-in reader may send it; `known()`
// whether the page knows yet who reads; `kept()` is the error a caller gets for a save kept while
// writes are paused. All five come from `client.js`.
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
// The answer the site gives a save naming a crop it no longer holds.
const GONE = { response: new Response(null, { status: 409 }), value: { code: 'reload' } }
/** Refuse a gone save to the view waiting on it and drop it, or keep it as gone for the corner. */
async function refuseGone(entry, answer) {
  let error
  try { sender.settle(answer) } catch (refusal) { error = refusal }
  refuse(entry.key)
  if (settleWaiters(entry.key, waiter => waiter.reject(error)) || discarded.has(entry.key)) await remove(entry.key)
  else await put({ ...entry, gone: true })
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
      // The reader may have discarded it, or another tab sent it or found it gone, since it was read.
      if (discarded.has(entry.key)) continue
      const now = await stored(entry.key)
      // One another tab found gone, which a view here waits on, is refused to that view.
      if (now?.gone && waiting.has(entry.key)) { await refuseGone(entry, GONE); continue }
      if (now ? now.gone : !waiting.has(entry.key)) continue
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
      // A save naming a crop the site no longer holds goes to the view that made it, which shows the
      // refusal; one from an earlier page stays kept as gone, for the reader to see and discard.
      if (isReload(answer.response, answer.value)) { await refuseGone(entry, answer); continue }
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

/** Close a refusal in the corner; a gone save it names is discarded from the device. */
export async function dismiss(key) {
  const gone = outbox.refused.some(r => r.key === key && r.gone)
  outbox.refused = outbox.refused.filter(r => r.key !== key)
  if (!gone) return
  discarded.add(key)
  await remove(key)
  await count()
}
