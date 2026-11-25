import { request } from './client.js'
import { textParts } from './cropReadings.js'
import { runText } from './runs.js'

// As many crops as one reading of them takes (`READ_MAX`, `apps/cloudflare/src/routes/claims.ts`).
const READ_MAX = 48
const PAGE = 10

/** The run of a reading as the site's run search takes it, else '': two to eight characters. */
export const placesText = value => value ? runText(value.run.join('')) : ''
/** The book a crop's page is of, as a run's books are named. */
export const bookOf = crop => crop.page_id?.replace(/:\d+$/, '') || null

/** A page of the places a run is written: `{ items, total, next_offset, works }`. */
export const runPlaces = (text, { document = null, offset = 0 } = {}) => {
  const limit = Math.min(PAGE, Math.floor(READ_MAX / textParts(text).length))
  return request('/atlas/runs?' + new URLSearchParams({ text, sort: 'source', offset, limit, ...(document ? { document } : {}) }))
}
/** How some crops are read now, by crop: `{ id, crop_version, text, reading, word }`. */
export async function readingsNow(ids) {
  const { items } = await request('/atlas/readings?crops=' + ids.join(','))
  return new Map(items.map(item => [item.id, item]))
}

/**
 * The places of a run other than the crop's own, each with how it is read now. A place is `open` to a
 * reading while none of its crops carries one, each can be claimed, and its page can be seen around it; else it
 * shows what it is `read` as. Its run is in the characters it is written with.
 */
export function readingPlaces(items, reads, own) {
  return items.filter(item => !item.crops.some(crop => crop.id === own)).map(item => {
    const crops = item.crops.map(crop => ({ ...crop, ...reads.get(crop.id) }))
    const run = crops.map(crop => (crop.written_character ?? crop.label ?? '').normalize('NFC'))
    const read = crops.find(crop => crop.reading?.values.length)?.reading ?? null
    const open = !read && Boolean(item.page) && run.every(Boolean) && crops.every(crop => reads.get(crop.id)?.crop_version)
    return { id: crops[0].id, crops, run, read, open, page: item.page, vertical: item.vertical }
  })
}

/** What is saved at a place: the reading, over its own characters, on what the reader says it rests on. The ruby
 *  of another page is never carried. */
export function placeReading(place, value, { method, word }) {
  const [first, ...rest] = place.crops
  return [first, { run: place.run, at: 0, reading: value.reading, method, word, crops: new Map(rest.map((crop, i) => [i + 1, crop])) }]
}
