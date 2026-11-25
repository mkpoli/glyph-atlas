import { error, redirect } from '@sveltejs/kit'
import { citedCut } from './citedCut.js'
import { localize } from './i18n.svelte.js'
import { redirectPath } from './movedCrop.js'
import { OLD_URL_STATUS } from '../../../cloudflare/src/atlas3Crops.ts'

/** Server loader for `/crop/[id]`: redirects moved or retired ids to the crop's public address. */
export async function loadCropPage(read, { fetch, params, url }) {
  let record
  try {
    record = await read(params.id, { fetch })
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
  const target = redirectPath(record, params, url.search, localize)
  if (target) redirect(OLD_URL_STATUS, target)
  const token = url.searchParams.get('v')
  return { record, cited: token ? await citedCut(record, token, fetch) : null }
}
