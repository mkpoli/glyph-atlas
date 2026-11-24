import { error, redirect } from '@sveltejs/kit'
import { expandFor, graphemeAddress, graphemeOf, unslug } from './addresses.js'
import { cardGallery, galleryCard } from './gallery.js'
import { keyText } from './codePoints.js'
import { runsWith } from './frequentRuns.js'
import { localize } from './i18n.svelte.js'

/**
 * Server loader of a character's page and a grapheme's (`of`): its gallery with the scope, visual group,
 * style group, order and years the address asks for. A grapheme has one page, under its head, and an
 * address naming another of its members leads there.
 */
export async function loadGallery(of, { fetch, params, url }) {
  const codePoint = unslug(params.code)
  if (!codePoint) error(404, 'Not a character address.')
  const asked = name => url.searchParams.get(name) ?? ''
  // A busy database is named, so the page loads itself again once it is back.
  const refused = e => error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  // The frequent runs the character is part of, shown with its gallery; the view reads them itself if they do not come.
  const runs = runsWith(keyText(codePoint), { fetch, signal: AbortSignal.timeout(5000) }).catch(() => null)
  const card = await galleryCard(codePoint, { fetch }).catch(refused), head = graphemeOf(card)
  if (of === 'grapheme' && head.code_point !== card.code_point) redirect(307, localize(graphemeAddress(head.code_point, head.char), params.lang) + url.search)
  const gallery = await cardGallery(card, expandFor(of, asked('scope')),
    { visual: asked('visual'), style: asked('style'), order: asked('order'), years: asked('years') }, { fetch }).catch(refused)
  return { gallery: { ...gallery, runs: await runs } }
}
