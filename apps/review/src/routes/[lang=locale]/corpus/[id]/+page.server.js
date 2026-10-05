import { error } from '@sveltejs/kit'
import { corpusCharacter } from '$lib/client.js'
import { citedCut } from '$lib/citedCut.js'

// A citation's address names the cut it cites (`?v=`); the page notes it when the glyph was cut again since.
export async function load({ fetch, params, url }) {
  let record
  try {
    record = await corpusCharacter(params.id, { fetch })
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
  const token = url.searchParams.get('v')
  return { record, cited: token ? await citedCut(record, token, fetch) : null }
}
