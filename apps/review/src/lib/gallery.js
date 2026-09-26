import { character as layerCharacter, occurrences, candidates as layerCandidates } from './layers.js'
import { writtenLabel } from './identity.js'

// A character's gallery: the occurrences this collection holds and the located glyphs the corpus index
// knows about. The page server renders it and the collection view loads it the same way.

/** A code point or a sequence (`U+304B U+309A`) as it reads in an address: `U+304B-U+309A`. */
export const slug = codePoint => codePoint.trim().split(/\s+/).join('-')
/** An address's code points, or null when the address names none. */
export const unslug = value => /^U\+[0-9A-F]{4,6}(-U\+[0-9A-F]{4,6})*$/.test(value) ? value.split('-').join(' ') : null

/** Whether a character opens on its whole family: the card says so, or its crops only exist there. */
export const widensByDefault = card => card.default_scope === 'grapheme' || Boolean(card.candidates?.requires_family_scope)

/**
 * The scope an address asks for: `family` is the character's grapheme family, `exact` the character
 * alone. Without one the card's default applies, and the address leaves it out.
 */
export const expandFor = (scope, card) => scope === 'family' ? 'grapheme' : scope === 'exact' ? 'none' : widensByDefault(card) ? 'grapheme' : 'none'
export const scopeFor = (expand, card) => (expand === 'grapheme') === widensByDefault(card) ? null : expand === 'grapheme' ? 'family' : 'exact'

/** The page of a character in the collection view, with the scope and visual group it is shown with. */
export function characterAddress(codePoint, { scope = null, visual = '' } = {}) {
  const query = new URLSearchParams(Object.entries({ scope, visual }).filter(([, value]) => value))
  return '/character/' + slug(codePoint) + (query.size ? '?' + query : '')
}

const bare = { char: '', characters: [], derived: [], jibo: [], expansions: [], candidates: null }

/**
 * Everything the collection view shows for one character: its card, its first page of occurrences and
 * of corpus leads, and the counts beside them. A corpus that cannot answer leaves `corpusFault` set and
 * the occurrences still shown.
 */
export async function characterGallery(codePoint, { scope = null, visual = '' } = {}, options = {}) {
  const card = { ...bare, ...await layerCharacter(codePoint, 'none', options) }
  const expand = expandFor(scope, card)
  const [found, widened, leads] = await Promise.all([
    occurrences(codePoint, { expand, limit: 60, offset: 0 }, options),
    // The chips read which widening is in force from the card fetched with it.
    expand === 'none' ? card : layerCharacter(codePoint, expand, options).catch(() => card),
    layerCandidates(codePoint, 60, 0, { scope: expand === 'grapheme' ? 'grapheme' : 'character', visual_group: visual || undefined }, options)
      .catch(error => ({ fault: error.status === 502 ? 'error' : 'not-loaded' })),
  ])
  const corpus = (leads.glyph_items ?? []).map(item => ({ ...item, label: writtenLabel(item), origin: 'corpus' }))
  return {
    picked: { ...card, ...widened },
    expand,
    visual,
    local: found.items.map(item => ({ ...item, origin: 'collection' })),
    total: found.counts.total,
    available: found.counts.exact_total,
    corpus,
    corpusTotal: leads.glyphs ?? 0,
    corpusFault: leads.fault ?? null,
    analysis: leads.visual_analysis ?? card.visual_analysis ?? null,
    familyTotal: leads.family_total ?? null,
    unassignedCount: leads.unassigned_count ?? null,
  }
}
