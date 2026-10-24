import { error } from '@sveltejs/kit'

// The page is sent at once; the browser reads the family on show, then the family list. The bare
// /forms address shows the first family once the list arrives.
export async function load({ params, parent }) {
  if (!(await parent()).forms) error(404, 'No clustering has been published.')
  return { family: params.family ?? '' }
}
