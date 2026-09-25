import { request } from './client.js'

// The page photos and the boxes on them. Only the local review service serves these routes; the
// hosted Worker answers 404, so the Pages view and its drawing tools stay hidden there.
export async function pagesAvailable() {
  try {
    const response = await fetch('/atlas/pages', { method: 'GET' })
    return response.ok && (response.headers.get('content-type') || '').includes('application/json')
  } catch { return false }
}
export const pageList = () => request('/atlas/pages')
export const pageRecord = id => request('/atlas/pages/' + encodeURIComponent(id))
export const drawBox = (pageId, body) => request('/atlas/pages/' + encodeURIComponent(pageId) + '/units', body)
/** A character named for a drawn box goes through the character layer, as any identity correction does. */
export const nameCharacter = (unitId, body) => request('/layers/units/' + encodeURIComponent(unitId), body)
/** Retiring keeps the unit and its history in the journal; it only leaves the page. */
export const retireBox = (unit, clientId) => request('/reviews', {
  target_type: 'unit', target_id: unit.id, field: 'active', new: false,
  base_revision: unit.revision, client_id: clientId, idempotency_key: 'retire:' + unit.id + ':' + unit.revision,
})
