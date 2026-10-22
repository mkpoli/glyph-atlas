import { request } from './client.js'
import { character as readCard } from './layers.js'
import { withGraphemeClaim } from './identity.js'

const pointOf = char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
const headOf = card => card?.grapheme ? { char: card.grapheme.char, code_point: card.grapheme.code_point } : null

const cards = new Map()
/** The graphemes a crop written as `char` may be read as: the one `char` is filed under, then each
 *  other one it writes (后 writes 後). A character the layer does not know offers itself alone. */
export function graphemeChoices(char) {
  if (!char) return Promise.resolve({ own: null, writes: [] })
  if (!cards.has(char)) {
    const found = readCard(pointOf(char)).then(card => ({
      own: headOf(card) ?? { char, code_point: pointOf(char) },
      writes: (card?.variants?.writes ?? []).map(item => ({ char: item.char, code_point: item.code_point })),
    }), error => error?.status === 404 ? { own: { char, code_point: pointOf(char) }, writes: [] } : Promise.reject(error))
    // A failed read is asked again next time rather than kept.
    cards.set(char, found.catch(error => { cards.delete(char); throw error }))
  }
  return cards.get(char)
}

/** The grapheme a character found by search is filed under. */
export async function graphemeOf(item) {
  const card = await readCard(item.code_point ?? pointOf(item.char)).catch(error => error?.status === 404 ? null : Promise.reject(error))
  return headOf(card) ?? { char: item.char, code_point: item.code_point ?? pointOf(item.char) }
}

// A lost answer keeps its submission id; an answered save of the same value is a new claim.
const submissions = new Map()
/** Read `crop` as `grapheme` (a key, `U+5F8C`), or with null take back the reader's own choice. The crop
 *  answered is filed under the grapheme claimed, or `own`, its character's, once none holds. */
export async function setGrapheme(crop, grapheme, own = null) {
  const key = JSON.stringify([crop.id, crop.crop_version, grapheme])
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/grapheme`,
      { id: submissions.get(key), crop_version: crop.crop_version, grapheme })
  } catch (error) { if (error.status) submissions.delete(key); throw error }
  submissions.delete(key)
  return withGraphemeClaim(crop, result.grapheme_claim, own)
}
