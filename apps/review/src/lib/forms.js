import { request } from './client.js'

// Both the local review server and the hosted Worker serve form assignment once a clustering exists;
// without one the path answers 404 and the view stays hidden. The families' code points, or null.
export async function familyCodes(send = fetch) {
  try {
    const response = await send('/atlas/forms/families', { method: 'GET' })
    if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) return null
    return (await response.json()).items.map(item => item.code_point)
  } catch { return null }
}
export const families = options => request('/atlas/forms/families', undefined, options)
export const family = (codePoint, order = 'shape', options) =>
  request('/atlas/forms/families/' + encodeURIComponent(codePoint) + '?' + new URLSearchParams({ order }), undefined, options)
export const members = (cluster, offset = 0, limit = 120, order = 'typical') =>
  request('/atlas/forms/clusters/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ offset, limit, order }))
export const decide = decision => request('/atlas/forms/decisions', decision)
export const split = (cluster, k) =>
  request('/atlas/forms/split/' + cluster.split('/').map(encodeURIComponent).join('/') + '?' + new URLSearchParams({ k }))

/** A family's palette: its encoded forms, then the descriptions its glyphs are already named with (a
 *  shape Unicode lacks, written as an ideographic description sequence), most used first. */
export function paletteOf(family) {
  const forms = family?.forms ?? [], seen = new Set(forms.map(form => form.char))
  return [...forms, ...(family?.described ?? []).filter(form => !seen.has(form.char)).map(form => ({ ...form, described: true }))]
}
