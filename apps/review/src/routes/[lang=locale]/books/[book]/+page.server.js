import { error } from '@sveltejs/kit'
import { request } from '$lib/client.js'

// One book with its pages, read on the server so the address can be linked and reloaded.
export async function load({ parent, params, fetch }) {
  if (!(await parent()).books) error(404, 'The books are served by the local review service only.')
  try {
    return { book: await request('/atlas/books/' + encodeURIComponent(params.book), undefined, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
