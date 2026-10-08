import { error } from '@sveltejs/kit'
import { catalogue } from '$lib/client.js'
import { characterGallery, unslug } from '$lib/gallery.js'
import { keyText } from '$lib/codePoints.js'
import { runsWith } from '$lib/frequentRuns.js'

// One character's gallery, with the scope, visual group, style group, order and years the address asks for.
export async function load({ fetch, params, url }) {
  const codePoint = unslug(params.code)
  if (!codePoint) error(404, 'Not a character address.')
  try {
    const [gallery, summary, runs] = await Promise.all([
      characterGallery(codePoint, { scope: url.searchParams.get('scope'), visual: url.searchParams.get('visual') ?? '',
        style: url.searchParams.get('style') ?? '', order: url.searchParams.get('order') ?? '', years: url.searchParams.get('years') ?? '' }, { fetch }),
      // The collection's totals, for the reading list and the footer's count, as the collection page has them.
      catalogue({ limit: 1 }, { fetch }).catch(() => null),
      // The frequent runs the character is part of, shown with its gallery; the view reads them itself if they do not come.
      runsWith(keyText(codePoint), { fetch, signal: AbortSignal.timeout(5000) }).catch(() => null),
    ])
    return { gallery: { ...gallery, summary, runs } }
  } catch (e) {
    // A busy database is named, so the page loads itself again once it is back.
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
