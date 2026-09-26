import { error, redirect } from '@sveltejs/kit'
import { character } from '$lib/client.js'

export async function load({ fetch, params }) {
  try {
    return { record: await character(params.id, { fetch }) }
  } catch (e) {
    // A crop a publication retired has moved to the crop that replaced it.
    if (e.replacedBy) redirect(301, '/character/' + encodeURIComponent(e.replacedBy))
    error(e.status === 404 ? 404 : 503, e.message)
  }
}
