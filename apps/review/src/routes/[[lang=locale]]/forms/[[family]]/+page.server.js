import { error, redirect } from '@sveltejs/kit'
import { families, family } from '$lib/forms.js'

// The family list and one family's clusters. The bare /forms address moves to the first family, so
// each family's page has one address.
export async function load({ fetch, params, parent }) {
  if (!(await parent()).forms) error(404, 'No clustering has been published.')
  const list = (await families({ fetch })).items
  if (!params.family) {
    if (!list.length) return { initial: { list, family: null } }
    redirect(307, '/forms/' + list[0].code_point)
  }
  const code = params.family
  try {
    return { initial: { list, family: await family(code, 'shape', { fetch }) } }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, e.message)
  }
}
