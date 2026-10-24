import { error } from '@sveltejs/kit'
import { familyCodes } from '$lib/forms.js'

// The page is sent at once; the browser reads the family on show, then the family list. The bare
// /forms address shows the first family once the list arrives. An address naming no family is a 404,
// checked against the family code points the edge keeps.
export async function load({ fetch, params, parent }) {
  const [{ forms }, codes] = await Promise.all([parent(), params.family ? familyCodes(fetch) : null])
  if (!forms) error(404, 'No clustering has been published.')
  if (params.family && !codes?.includes(params.family)) error(404, 'This family was not clustered.')
  return { family: params.family ?? '' }
}
