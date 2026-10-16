import { error } from '@sveltejs/kit'
import { request } from '$lib/client.js'

// A page is served by the site and by the local review service; the list of every page by the local one only.
export async function load({ parent, params, fetch }) {
  if (!params.id) {
    if (!(await parent()).pages) error(404, 'The list of pages is served by the local review service only.')
    return { record: null }
  }
  try {
    return { record: await request('/atlas/pages/' + encodeURIComponent(params.id), undefined, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
