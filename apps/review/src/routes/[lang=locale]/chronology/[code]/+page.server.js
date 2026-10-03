import { error } from '@sveltejs/kit'
import { unslug } from '$lib/gallery.js'
import { character } from '$lib/layers.js'
import { chronology, chronologyOptions } from '$lib/chronology.js'

// A character's time axis, placed and narrowed as the address asks.
export async function load({ fetch, params, url }) {
  const codePoint = unslug(params.code)
  if (!codePoint) error(404, 'Not a character address.')
  const options = chronologyOptions(url.searchParams)
  try {
    const card = await character(codePoint, options.scope === 'grapheme' ? 'grapheme' : 'none', { fetch })
    const chars = options.scope === 'grapheme' && options.script
      ? (card.characters ?? []).filter(c => c.script === options.script).map(c => c.char) : []
    return { card, options, axis: await chronology(codePoint, { ...options, chars }, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
