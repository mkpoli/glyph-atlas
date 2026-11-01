import { error } from '@sveltejs/kit'

// The book list is served by the local review service, which is also the only backend that serves
// page photos. The query is in the address, so a search can be linked and reloaded.
export async function load({ parent, url }) {
  if (!(await parent()).books) error(404, 'The books are served by the local review service only.')
  return { query: url.searchParams.get('q')?.trim() ?? '' }
}
