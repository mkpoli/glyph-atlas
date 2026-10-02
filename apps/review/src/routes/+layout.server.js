import { familyCodes } from '$lib/forms.js'
import { pagesAvailable } from '$lib/pages.js'
import { providers, viewer } from '../../../cloudflare/src/auth.ts'

// Which optional views this backend serves: forms once a clustering is published (with the code points
// that have a family, so a character page can link to its own), page photos only on the local review service.
// Who is signed in comes from the session's signed cookie, so the header draws it in the first paint.
export async function load({ fetch, platform, request }) {
  const [forms, pages, user] = await Promise.all([familyCodes(fetch), pagesAvailable(fetch),
    platform?.env ? viewer(platform.env, request).catch(() => null) : null])
  return { forms, pages, account: { user, providers: platform?.env ? providers(platform.env) : [] } }
}
