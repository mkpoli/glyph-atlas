import { error } from '@sveltejs/kit'
import { corpusCharacter } from '$lib/client.js'

export async function load({ fetch, params }) {
  try {
    return { record: await corpusCharacter(params.id, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, e.message)
  }
}
