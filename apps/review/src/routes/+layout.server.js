import { formsAvailable } from '$lib/forms.js'
import { pagesAvailable } from '$lib/pages.js'

// Which optional views this backend serves: forms once a clustering is published, page photos only
// on the local review service.
export async function load({ fetch }) {
  const [forms, pages] = await Promise.all([formsAvailable(fetch), pagesAvailable(fetch)])
  return { forms, pages }
}
