import { request } from './client.js'

// Both the local review server and the hosted Worker serve form assignment once a clustering exists;
// without one the path answers 404 and the view stays hidden.
export async function formsAvailable() {
  try {
    const response = await fetch('/atlas/forms/families', { method: 'GET' })
    return response.ok && (response.headers.get('content-type') || '').includes('application/json')
  } catch { return false }
}
export const families = () => request('/atlas/forms/families')
export const family = (codePoint, order = 'shape') => request('/atlas/forms/families/' + encodeURIComponent(codePoint) + '?' + new URLSearchParams({ order }))
export const members = (cluster, offset = 0, limit = 120, order = 'typical') =>
  request('/atlas/forms/clusters/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ offset, limit, order }))
export const decide = decision => request('/atlas/forms/decisions', decision)
export const report = body => request('/atlas/forms/reports', body)
export const split = (cluster, k) =>
  request('/atlas/forms/split/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ k }))
