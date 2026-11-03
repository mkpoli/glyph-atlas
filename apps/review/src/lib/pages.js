import { request } from './client.js'

// The books and the page photos of a dataset. Only the local review service serves these routes; the
// hosted Worker answers 404, so the Books view and the drawing tools stay hidden there.
export async function booksAvailable(send = fetch) {
  try {
    const response = await send('/atlas/books?limit=1', { method: 'GET' })
    return response.ok && (response.headers.get('content-type') || '').includes('application/json')
  } catch { return false }
}
/** The books a query finds, `offset` books on from the first. A blank query finds every book. */
export const bookList = (query = '', offset = 0) =>
  request('/atlas/books?' + new URLSearchParams({ q: query, offset: String(offset) }))
export const bookRecord = id => request('/atlas/books/' + encodeURIComponent(id))
export const pageRecord = id => request('/atlas/pages/' + encodeURIComponent(id))
export const drawBox = (pageId, body) => request('/atlas/pages/' + encodeURIComponent(pageId) + '/units', body)
/** A character named for a drawn box goes through the character layer, as any identity correction does. */
export const nameCharacter = (unitId, body) => request('/layers/units/' + encodeURIComponent(unitId), body)
/** Keeping a proposed box records that a person saw a mark there, so a newer proposer never retires it. */
export const keepBox = unit => request('/reviews', {
  target_type: 'unit', target_id: unit.id, field: 'review', new: 'reviewed',
  base_revision: unit.revision, idempotency_key: 'keep:' + unit.id + ':' + unit.revision,
})
/** Retiring keeps the unit and its history in the journal; it only leaves the page. */
export const retireBox = unit => request('/reviews', {
  target_type: 'unit', target_id: unit.id, field: 'active', new: false,
  base_revision: unit.revision, idempotency_key: 'retire:' + unit.id + ':' + unit.revision,
})
