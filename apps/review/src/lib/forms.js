import { request } from './client.js'

// The form-assignment API exists only on the local review server. The published site answers this
// path with its single-page HTML, so a JSON answer is what shows the view is available.
export async function formsAvailable() {
  try {
    const response = await fetch('/forms/families', { method: 'GET' })
    return response.ok && (response.headers.get('content-type') || '').includes('application/json')
  } catch { return false }
}
export const families = () => request('/forms/families')
export const family = (codePoint, order = 'shape') => request('/forms/families/' + encodeURIComponent(codePoint) + '?' + new URLSearchParams({ order }))
export const members = (cluster, offset = 0, limit = 120, order = 'typical') =>
  request('/forms/clusters/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ offset, limit, order }))
export const decide = decision => request('/forms/decisions', decision)
export const report = body => request('/forms/reports', body)
export const split = (cluster, k) =>
  request('/forms/split/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ k }))
