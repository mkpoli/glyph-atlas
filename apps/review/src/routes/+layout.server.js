import { familyCodes } from '$lib/forms.js'
import { pagesAvailable } from '$lib/pages.js'
import { providers, viewer } from '../../../cloudflare/src/auth.ts'

// Which optional views this backend serves: forms once a clustering is published, page photos only on
// the local review service. Both change only with a publication, so a server asks every few minutes,
// and sooner while a view is missing in case the answer was a passing failure.
const VIEWS_TTL = 5 * 60_000, MISSING_TTL = 30_000
let views = null
function optionalViews(fetch) {
  const age = Date.now() - (views?.at ?? 0)
  if (!views || age > VIEWS_TTL || (views.missing && age > MISSING_TTL)) {
    const asked = { at: Date.now(), missing: true }
    asked.found = Promise.all([familyCodes(fetch), pagesAvailable(fetch)])
      .then(([codes, pages]) => { asked.missing = codes === null; return { forms: codes !== null, pages } })
    views = asked
  }
  return views.found
}

// Who is signed in comes from the session's signed cookie, so the header draws it in the first paint.
export async function load({ fetch, platform, request }) {
  const [{ forms, pages }, user] = await Promise.all([optionalViews(fetch),
    platform?.env ? viewer(platform.env, request).catch(() => null) : null])
  return { forms, pages, account: { user, providers: platform?.env ? providers(platform.env) : [] } }
}
