import { character, afterWrite } from './client.js'

// Crops read ahead of the reader: when one opens, its neighbours in the list are read and their
// images warmed, so ← → and "next after saving" show the next crop at once. An entry lives twenty
// seconds, and every write empties the cache, since a write may change any crop (a bulk correction,
// a round, an undo).
const kept = new Map()
const LIFE = 20000, ROOM = 12
afterWrite(() => kept.clear())

function warm(record) {
  if (record.proxyable === false || typeof Image === 'undefined') return
  for (const src of [record.image, record.context_image]) if (src) { const image = new Image(); image.decoding = 'async'; image.src = src }
}

/** Read a crop and warm its images, unless it was read a moment ago. */
export function prefetchCrop(id) {
  const hit = kept.get(id)
  if (hit && Date.now() - hit.at < LIFE) return
  // A read ahead does not wait out a busy database; the crop is read again when it is opened.
  const promise = character(id, { wait: false }).then(record => { warm(record); return record })
  promise.catch(() => { if (kept.get(id)?.promise === promise) kept.delete(id) })
  kept.set(id, { at: Date.now(), promise })
  while (kept.size > ROOM) kept.delete(kept.keys().next().value)
}

/** The crop, from what was read ahead when it is fresh, else from the service. */
export function readCrop(id) {
  const hit = kept.get(id)
  return hit && Date.now() - hit.at < LIFE ? hit.promise.catch(() => character(id)) : character(id)
}

/** A crop was written: what was read ahead of it is stale. */
export function forgetCrop(id) {
  kept.delete(id)
}
