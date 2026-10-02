import { character, corpusCharacter } from './client.js'

// Crops read ahead of the reader: when one opens, its neighbours in the list are read and their
// images warmed, so ← → and "next after saving" show the next crop at once. An entry is used once,
// lives a minute, and goes when its crop is written.
const kept = new Map()
const LIFE = 60000, ROOM = 12
const keyOf = (id, origin) => (origin === 'corpus' ? 'corpus:' : 'crop:') + id
const read = (id, origin) => origin === 'corpus' ? corpusCharacter(id) : character(id)

function warm(record) {
  if (record.proxyable === false || typeof Image === 'undefined') return
  for (const src of [record.image, record.context_image]) if (src) { const image = new Image(); image.decoding = 'async'; image.src = src }
}

/** Read a crop and warm its images, unless it was read a moment ago. */
export function prefetchCrop(id, origin = 'collection') {
  const key = keyOf(id, origin), hit = kept.get(key)
  if (hit && Date.now() - hit.at < LIFE) return
  const promise = read(id, origin).then(record => { warm(record); return record })
  promise.catch(() => { if (kept.get(key)?.promise === promise) kept.delete(key) })
  kept.set(key, { at: Date.now(), promise })
  while (kept.size > ROOM) kept.delete(kept.keys().next().value)
}

/** The crop, from what was read ahead when it is fresh, else from the service. */
export function readCrop(id, origin = 'collection') {
  const key = keyOf(id, origin), hit = kept.get(key)
  kept.delete(key)
  return hit && Date.now() - hit.at < LIFE ? hit.promise : read(id, origin)
}

/** A crop was written: what was read ahead of it is stale. */
export function forgetCrop(id) {
  kept.delete(keyOf(id, 'collection'))
  kept.delete(keyOf(id, 'corpus'))
}
