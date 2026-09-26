import { error } from '@sveltejs/kit'
import { families, family } from '$lib/forms.js'

// The family list and one family's clusters; without a family in the address, the first in the list.
export async function load({ fetch, params, parent }) {
  if (!(await parent()).forms) error(404, 'No clustering has been published.')
  const list = (await families({ fetch })).items
  const code = params.family ?? list[0]?.code_point
  if (!code) return { initial: { list, family: null } }
  try {
    return { initial: { list, family: await family(code, 'shape', { fetch }) } }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, e.message)
  }
}
