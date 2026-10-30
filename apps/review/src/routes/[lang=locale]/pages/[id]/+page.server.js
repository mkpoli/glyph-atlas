import { error } from '@sveltejs/kit'
import { request } from '$lib/client.js'

// A page is served by the site and by the local review service.
export async function load({ params, fetch }) {
  try {
    return { record: await request('/atlas/pages/' + encodeURIComponent(params.id), undefined, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
