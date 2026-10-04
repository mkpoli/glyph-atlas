import { ensureSignedIn } from './session.svelte.js'
import { t } from './i18n.svelte.js'

// The crops the signed-in user has starred. Read once the page knows who is signed in, and again
// whenever that changes; a browser that has never signed in has starred nothing.
// `loaded` is whose stars `ids` holds, once a read has answered.
const state = $state({ ids: new Set(), user: null, loaded: null })
// The last state asked for each crop whose save has not finished, which a read must not undo, and
// each crop's saves in order, so a star and an unstar never cross on the way.
const pending = new Map(), queues = new Map()
// Saves finished so far; a read that a save overtook is read again.
let saved = 0

export const isFavourite = id => state.ids.has(id)
/** Whether the crop was starred and no longer is, as far as the stars read so far tell. */
export const isUnstarred = id => state.loaded !== null && state.loaded === state.user && !state.ids.has(id)

/** Read the user's stars, unless they are already held; `again` reads them afresh. */
export async function loadFavourites(user, { again = false } = {}) {
  const id = user?.id ?? null
  if (id !== state.user) { state.user = id; state.loaded = null; state.ids = new Set() }
  if (!id || (state.loaded === id && !again)) return
  const before = saved
  try {
    const response = await fetch('/api/favourites')
    if (!response.ok) throw new Error(response.statusText)
    const ids = new Set((await response.json()).ids)
    if (state.user !== id) return
    if (saved !== before) return loadFavourites(user, { again: true })
    for (const [crop, favourite] of pending) favourite ? ids.add(crop) : ids.delete(crop)
    state.ids = ids
    state.loaded = id
  } catch {
    // Not loaded, so the next call reads again.
    if (state.user === id) state.loaded = null
  }
}

const show = (id, favourite) => {
  const ids = new Set(state.ids)
  favourite ? ids.add(id) : ids.delete(id)
  state.ids = ids
}

/** Star a crop or take its star away, starting an anonymous session if there is none. After a failed
 *  save the stars are read again once the crop's saves have finished. */
export function setFavourite(id, favourite) {
  // A save belongs to whoever was signed in when it was asked for; it is dropped if someone else is
  // by the time it is sent.
  const owner = state.user
  show(id, favourite)
  pending.set(id, favourite)
  const run = (queues.get(id) ?? Promise.resolve()).catch(() => {}).then(async () => {
    const user = await ensureSignedIn()
    if (owner !== null && user?.id !== owner) throw new Error(t('favourites.otherUser'))
    if (state.user !== (user?.id ?? null)) { state.user = user?.id ?? null; state.loaded = null }
    const response = await fetch('/api/favourites', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ crop: id, favourite }) })
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? response.statusText)
  })
  queues.set(id, run)
  let failed = false
  return run.catch(error => { failed = true; throw error }).finally(() => {
    saved++
    if (queues.get(id) !== run) return
    queues.delete(id); pending.delete(id)
    if (failed) loadFavourites({ id: state.user }, { again: true })
  })
}
