import { request } from './client.js'

// Both the local review server and the hosted Worker serve form assignment once a clustering exists;
// without one the path answers 404 and the view stays hidden.
export async function formsAvailable(send = fetch) {
  try {
    const response = await send('/atlas/forms/families', { method: 'GET' })
    return response.ok && (response.headers.get('content-type') || '').includes('application/json')
  } catch { return false }
}
export const families = options => request('/atlas/forms/families', undefined, options)
export const family = (codePoint, order = 'shape', options) =>
  request('/atlas/forms/families/' + encodeURIComponent(codePoint) + '?' + new URLSearchParams({ order }), undefined, options)
export const members = (cluster, offset = 0, limit = 120, order = 'typical') =>
  request('/atlas/forms/clusters/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ offset, limit, order }))
export const decide = decision => request('/atlas/forms/decisions', decision)
export const split = (cluster, k) =>
  request('/atlas/forms/split/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ k }))
