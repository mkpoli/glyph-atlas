import { error, redirect } from '@sveltejs/kit'
import { citedCut } from './citedCut.js'
import { localize } from './i18n.svelte.js'
import { redirectPath } from './movedCrop.js'
import { OLD_URL_STATUS } from '../../../cloudflare/src/atlas3Crops.ts'

/** Shared server loader for `/crop/[id]` and `/corpus/[id]`: redirects moved ids and misrouted corpus glyphs. */
export async function loadCropPage(read, routeOrigin, { fetch, params, url }) {
  let record
  try {
    record = await read(params.id, { fetch })
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
  // A corpus glyph's address is `/corpus/...`; an old id, or a crop a publication retired, moves to the crop's public id.
  const target = redirectPath(record, routeOrigin, params, url.search, localize)
  if (target) redirect(OLD_URL_STATUS, target)
  const token = url.searchParams.get('v')
  return { record, cited: token ? await citedCut(record, token, fetch) : null }
}
