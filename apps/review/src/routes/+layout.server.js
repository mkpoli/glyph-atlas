import { familyCodes } from '$lib/forms.js'
import { pagesAvailable } from '$lib/pages.js'

// Which optional views this backend serves: forms once a clustering is published (with the code points
// that have a family, so a character page can link to its own), page photos only on the local review service.
export async function load({ fetch }) {
  const [forms, pages] = await Promise.all([familyCodes(fetch), pagesAvailable(fetch)])
  return { forms, pages }
}
