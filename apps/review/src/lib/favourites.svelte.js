import { ensureSignedIn } from './session.svelte.js'

// The crops the signed-in user has starred. Read once the page knows who is signed in, and again
// whenever that changes; a browser that has never signed in has starred nothing.
const state = $state({ ids: new Set(), user: null })
// Stars being saved, which a read that lands meanwhile must not undo.
const pending = new Map()
// Saves finished so far; a read that a save overtook is read again.
let saved = 0

export const isFavourite = id => state.ids.has(id)

/** Read the user's stars, unless they are the ones already held. */
export async function loadFavourites(user) {
  const id = user?.id ?? null
  if (id === state.user) return
  state.user = id
  if (!id) { state.ids = new Set(); return }
  await read(id)
}

async function read(id) {
  const before = saved
  try {
    const response = await fetch('/api/favourites')
    if (!response.ok || state.user !== id) return
    const ids = new Set((await response.json()).ids)
    if (saved !== before) return read(id)
    for (const [crop, favourite] of pending) favourite ? ids.add(crop) : ids.delete(crop)
    state.ids = ids
  } catch { /* The stars show once the next page reads them. */ }
}

/** Star a crop or take its star away, starting an anonymous session if there is none. */
export async function setFavourite(id, favourite) {
  const ids = new Set(state.ids)
  favourite ? ids.add(id) : ids.delete(id)
  state.ids = ids
  pending.set(id, favourite)
  try {
    const user = await ensureSignedIn()
    state.user = user?.id ?? null
    const response = await fetch('/api/favourites', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ crop: id, favourite }) })
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? response.statusText)
  } catch (error) {
    const ids = new Set(state.ids)
    favourite ? ids.delete(id) : ids.add(id)
    state.ids = ids
    throw error
  } finally {
    pending.delete(id)
    saved++
  }
}
