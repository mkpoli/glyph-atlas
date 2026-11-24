import { character as layerCharacter, occurrences, candidates as layerCandidates, bookGallery } from './layers.js'
import { writtenLabel } from './identity.js'
import { STYLE_GROUPS, styleParam } from './style.js'

// The gallery of a character (字体) or of a grapheme (字種): the occurrences this collection holds and the
// located glyphs the corpus index knows about. The page server renders it and the collection view loads
// it the same way.

/** The corpus side of a widening: the grapheme family, the character and its variants, or the character. */
export const corpusScope = expand => expand === 'grapheme' ? 'grapheme' : expand === 'variants' ? 'variants' : 'character'

const bare = { char: '', characters: [], derived: [], jibo: [], expansions: [], candidates: null }

/** The crops of each style group in the two lists together, or null when either list gave none. */
export function styleCounts(...lists) {
  if (lists.some(counts => !counts)) return null
  return Object.fromEntries(STYLE_GROUPS.map(group => [group, lists.reduce((sum, counts) => sum + (counts[group] ?? 0), 0)]))
}

/** An address's order (`year`, oldest first, or `book`, grouped by the books the crops come from) and year range (`1601-1700`, or `undated`), or '' for none. */
export const orderParam = value => ['year', 'book'].includes(value) ? value : ''
export function yearsParam(value) {
  if (value === 'undated') return value
  const found = /^(-?\d{1,4})-(-?\d{1,4})$/.exec(value ?? '')
  return found && Number(found[1]) <= Number(found[2]) ? value : ''
}

/** The scope a gallery grouped by book is read in. */
export const bookScope = expand => expand === 'grapheme' ? 'grapheme' : undefined
/** A page of a gallery grouped by book as its tiles, the collection's and the corpus's apart, and its
 *  books, each with how many crops it holds and the cursor of its next ones. */
export function bookPage(found) {
  const crops = found.groups.flatMap(group => group.items)
  return {
    local: crops.filter(item => item.origin !== 'corpus'),
    corpus: crops.filter(item => item.origin === 'corpus').map(item => ({ ...item, label: writtenLabel(item) })),
    books: found.groups.map(({ items, ...book }) => book),
  }
}

/** A character's card, with the fields the view reads where the card states none. */
export const galleryCard = async (codePoint, options = {}) => ({ ...bare, ...await layerCharacter(codePoint, 'none', options) })

/**
 * Everything the collection view shows for a card: its first page of occurrences and of corpus leads,
 * and the counts beside them. A corpus that cannot answer leaves `corpusFault` set and the occurrences
 * still shown.
 */
export async function cardGallery(card, expand, { visual = '', style = '', order = '', years = '' } = {}, options = {}) {
  const codePoint = card.code_point
  style = styleParam(style)
  // A variants widening lists each character in turn, and is placed by style alone.
  order = expand === 'variants' ? '' : orderParam(order); years = expand === 'variants' ? '' : yearsParam(years)
  const dated = { order: order || undefined, years: years || undefined }
  // The chips read which widening is in force from the card fetched with it; a variants widening
  // changes no field of the card.
  const widen = expand === 'none' || expand === 'variants' ? card : layerCharacter(codePoint, expand, options).catch(() => card)
  if (order === 'book') {
    const [found, widened] = await Promise.all([bookGallery(codePoint, { scope: bookScope(expand), style: style || undefined, years: dated.years }, options), widen])
    const { books, local, corpus } = bookPage(found), total = counts => Object.entries(counts).reduce((sum, [name, n]) => !style || name === style ? sum + n : sum, 0)
    return { picked: { ...card, ...widened }, expand, visual: '', style, order, years, styled: true, localStyles: found.styles.local, corpusStyles: found.styles.corpus,
      local, localAfter: null, total: total(found.styles.local), available: total(found.styles.local), withheld: found.withheld, corpus, corpusTotal: total(found.styles.corpus),
      corpusAfter: null, corpusFault: null, analysis: card.visual_analysis ?? null, familyTotal: null, unassignedCount: null, books, booksAfter: found.next }
  }
  const [found, widened, leads] = await Promise.all([
    occurrences(codePoint, { expand, limit: 60, style: style || undefined, ...dated }, options),
    widen,
    layerCandidates(codePoint, 60, null, { scope: corpusScope(expand), visual_group: visual || undefined, style: style || undefined, ...dated }, options)
      .catch(error => ({ fault: error.status === 502 ? 'error' : 'not-loaded' })),
  ])
  const corpus = (leads.glyph_items ?? []).map(item => ({ ...item, label: writtenLabel(item), origin: 'corpus' }))
  return {
    picked: { ...card, ...widened },
    expand,
    visual,
    style,
    order,
    years,
    // Only a server that files crops by style names its groups; the local review server does not.
    styled: Boolean(found.style_groups),
    localStyles: found.styles ?? null,
    corpusStyles: leads.fault ? {} : leads.styles ?? null,
    local: found.items.map(item => ({ ...item, origin: 'collection' })),
    localAfter: found.next ?? null,
    total: found.counts.total,
    available: found.counts.exact_total,
    withheld: found.withheld ?? null,
    corpus,
    corpusTotal: leads.glyphs ?? 0,
    corpusAfter: leads.next ?? null,
    corpusFault: leads.fault ?? null,
    analysis: leads.visual_analysis ?? card.visual_analysis ?? null,
    familyTotal: leads.family_total ?? null,
    unassignedCount: leads.unassigned_count ?? null,
  }
}
