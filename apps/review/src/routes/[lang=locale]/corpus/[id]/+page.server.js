import { error } from '@sveltejs/kit'
import { corpusCharacter } from '$lib/client.js'

export async function load({ fetch, params }) {
  try {
    return { record: await corpusCharacter(params.id, { fetch }) }
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
