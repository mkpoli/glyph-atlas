import { familyCodes } from '$lib/forms.js'
import { booksAvailable } from '$lib/pages.js'
import { providers, viewer } from '../../../cloudflare/src/auth.ts'

// Which optional views this backend serves: forms once a clustering is published, the books and page
// photos only on the local review service.
// Who is signed in comes from the session's signed cookie, so the header draws it in the first paint.
// `known` says the session was read, whoever it named; without it the browser asks.
export async function load({ fetch, platform, request }) {
  const unread = { user: null, known: false }
  const [forms, books, reader] = await Promise.all([familyCodes(fetch), booksAvailable(fetch),
    platform?.env ? viewer(platform.env, request).then(user => ({ user, known: true }), () => unread) : unread])
  return { forms: forms !== null, books, account: { ...reader, providers: platform?.env ? providers(platform.env) : [] } }
}
