import { keyText } from './codePoints.js'
import { hasCharacterPage } from './issues.js'

// The addresses of the collection, of a character (字体) and of a grapheme (字種). A page is of one of the
// three, and its address says which.

/** A code point or a sequence (`U+304B U+309A`) as it reads in an address: `U+304B-U+309A`. */
export const slug = codePoint => codePoint.trim().split(/\s+/).join('-')
/** An address's code points, or null when the address names none. */
export const unslug = value => /^U\+[0-9A-F]{4,6}(-U\+[0-9A-F]{4,6})*$/.test(value) && keyText(value.split('-').join(' ')) !== null
  ? value.split('-').join(' ') : null

/** The widening a page is shown with: a grapheme's page gathers every form of it, and a character's shows
 *  the character alone, or with its 異体字 (`scope=variants`). */
export const expandFor = (of, scope = null) => of === 'grapheme' ? 'grapheme' : scope === 'variants' ? 'variants' : 'none'
const pageAddress = (of, codePoint, { scope = null, visual = '', style = '', order = '', years = '' } = {}) => {
  const query = new URLSearchParams(Object.entries({ scope, visual, style, order, years }).filter(([, value]) => value))
  return `/${of}/${slug(codePoint)}` + (query.size ? '?' + query : '')
}
/** The page of one character, with the scope (`variants`), style group, order and years it is shown with. */
export const characterAddress = (codePoint, shown = {}) => pageAddress('character', codePoint, shown)

/** The page of a grapheme, by its key and the text it is shown as, with the visual group, style group,
 * order and years it is shown with; a member's key leads to its grapheme's page. A grapheme of more than
 * one character has no page of its own, so its address narrows the collection to it. */
export const graphemeAddress = (key, text, shown = {}) => hasCharacterPage(text) ? pageAddress('grapheme', key, shown) : collectionAddress({ grapheme: key })

/** The grapheme a card's character is a form of: a card that names none is its own. */
export const graphemeOf = card => card.grapheme ?? card
/** The page a card's gallery is on, as it is widened. */
export const galleryAddress = (card, expand, shown = {}) => expand === 'grapheme' ? pageAddress('grapheme', graphemeOf(card).code_point, shown)
  : characterAddress(card.code_point, { ...shown, scope: expand === 'variants' ? 'variants' : null })
/** The page a path names, `{ of: 'character' | 'grapheme', codePoint }`, or null. */
export function galleryPage(path) {
  const [, of, value] = /^\/(character|grapheme)\/([^/]+)$/.exec(path) ?? [], codePoint = of ? unslug(value) : null
  return codePoint ? { of, codePoint } : null
}

/** The form that holds every crop of a grapheme, whose page shows the same gallery; null where several
 *  forms hold crops or some are filed under the grapheme alone. A grapheme of no crop has one only as its
 *  single member. */
export function soleForm(grapheme) {
  const crops = v => (v.count ?? 0) + (v.corpus_count ?? 0), members = grapheme.members ?? [grapheme], used = members.filter(crops)
  return used.length === 1 && crops(used[0]) === crops(grapheme) ? used[0] : !used.length && !crops(grapheme) && members.length === 1 ? members[0] : null
}
/** The address search engines keep for a page, without its language: a page whose gallery is another
 *  page's names that one, so a grapheme of one form is indexed at the form's. */
export function canonicalAddress(card, expand) {
  const sole = expand === 'grapheme' ? soleForm(graphemeOf(card)) : null
  return sole ? characterAddress(sole.code_point) : galleryAddress(card, expand === 'variants' ? 'none' : expand)
}

/** The collection with a search and filters: a grapheme key, a work's document id and a script group. */
export function collectionAddress({ q = '', grapheme = '', work = '', group = 'all' } = {}) {
  const query = new URLSearchParams(Object.entries({ q, grapheme, work, group: group === 'all' ? '' : group }).filter(([, value]) => value))
  return '/' + (query.size ? '?' + query : '')
}
