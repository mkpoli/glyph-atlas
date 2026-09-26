import { formsAvailable } from '$lib/forms.js'
import { pagesAvailable } from '$lib/pages.js'

// The reader's language, and which optional views this backend serves: forms once a clustering is
// published, page photos only on the local review service.
export async function load({ fetch, locals }) {
  const [forms, pages] = await Promise.all([formsAvailable(fetch), pagesAvailable(fetch)])
  return { locale: locals.locale, forms, pages }
}
