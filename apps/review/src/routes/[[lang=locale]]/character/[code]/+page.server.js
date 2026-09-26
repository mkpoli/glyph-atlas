import { error } from '@sveltejs/kit'
import { characterGallery, unslug } from '$lib/gallery.js'

// One character's gallery, with the scope and visual group the address asks for.
export async function load({ fetch, params, url }) {
  const codePoint = unslug(params.code)
  if (!codePoint) error(404, 'Not a character address.')
  try {
    return { gallery: await characterGallery(codePoint, { scope: url.searchParams.get('scope'), visual: url.searchParams.get('visual') ?? '' }, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, e.message)
  }
}
