import { error, redirect } from '@sveltejs/kit'
import { character } from '$lib/client.js'
import { localize } from '$lib/i18n.svelte.js'
import { citedCut } from '$lib/citedCut.js'
import { OLD_URL_STATUS } from '../../../../../../cloudflare/src/atlas3Crops.ts'

// A citation's address names the cut it cites (`?v=`); the page notes it when the crop was cut again since.
export async function load({ fetch, params, url }) {
  let record
  try {
    record = await character(params.id, { fetch })
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
  // An old id, or a crop a publication retired, has moved to the crop's public id.
  if (record.id !== params.id) redirect(OLD_URL_STATUS, localize('/crop/' + encodeURIComponent(record.id), params.lang) + url.search)
  const token = url.searchParams.get('v')
  return { record, cited: token ? await citedCut(record, token, fetch) : null }
}
