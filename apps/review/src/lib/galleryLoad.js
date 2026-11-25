import { error, redirect } from '@sveltejs/kit'
import { expandFor, graphemeAddress, graphemeOf, unslug } from './addresses.js'
import { cardGallery, galleryCard } from './gallery.js'
import { keyText } from './codePoints.js'
import { hasCharacterPage } from './issues.js'
import { runsWith } from './frequentRuns.js'
import { localize } from './i18n.svelte.js'

/**
 * Server loader of a character's page and a grapheme's (`of`): its gallery with the scope, visual group,
 * style group, order and years the address asks for. A grapheme has one page, under its head, and an
 * address naming another of its members leads there. A grapheme of more than one character has none.
 */
export async function loadGallery(of, { fetch, params, url }) {
  const codePoint = unslug(params.code)
  if (!codePoint) error(404, 'Not a character address.')
  const asked = name => url.searchParams.get(name) ?? ''
  // A busy database is named, so the page loads itself again once it is back.
  const refused = e => error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  const card = await galleryCard(codePoint, { fetch }).catch(refused), head = graphemeOf(card)
  const narrowed = { visual: asked('visual'), style: asked('style'), order: asked('order'), years: asked('years') }
  if (of === 'grapheme' && !(head.code_point === card.code_point && hasCharacterPage(head.char))) {
    const [path, query] = graphemeAddress(head.code_point, head.char, narrowed).split('?')
    redirect(307, localize(path, params.lang) + (query ? '?' + query : ''))
  }
  const [gallery, runs] = await Promise.all([
    cardGallery(card, expandFor(of, asked('scope')), narrowed, { fetch }).catch(refused),
    // The frequent runs the character is part of, shown with its gallery; the view reads them itself if they do not come.
    runsWith(keyText(codePoint), { fetch, signal: AbortSignal.timeout(5000) }).catch(() => null),
  ])
  return { gallery: { ...gallery, runs } }
}
