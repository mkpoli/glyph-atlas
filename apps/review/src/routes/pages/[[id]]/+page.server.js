import { error } from '@sveltejs/kit'

// Page photos exist only on the local review service.
export async function load({ parent }) {
  if (!(await parent()).pages) error(404, 'Page photos are served by the local review service only.')
}
