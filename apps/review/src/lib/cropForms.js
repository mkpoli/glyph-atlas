import { request, character as readCrop, corpusCharacter } from './client.js'
import { character as readCard } from './layers.js'

/**
 * What a crop is written as, read and set through two calls the interface uses and nothing else:
 * `listForms` for the forms a crop may be marked as, and `setForm` to mark it. Today they sit on the
 * correction route Quick Review's form bar uses and the written-form route; the form ledger replaces
 * this module's body, not its callers.
 */

/** `U+3042` for あ; null for a sequence, which has no card of its own. */
const pointOf = char => [...char].length === 1 ? 'U+' + char.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') : null
const entry = item => ({ char: item.char, code_point: item.code_point ?? pointOf(item.char), script: item.script ?? '' })

const cards = new Map()
/**
 * The forms offered for a crop written as `char`: the members of its grapheme (は, ハ, 𛂞…), then the
 * variants the sources attest for `char` and the derived ones. A character the layer does not know
 * offers itself alone.
 */
export function listForms(char) {
  if (!char) return Promise.resolve({ members: [], variants: [], derived: [] })
  if (!cards.has(char)) {
    const point = pointOf(char)
    const loaded = (point ? readCard(point) : Promise.resolve(null)).then(card => {
      const members = (card?.grapheme?.members ?? [{ char }]).map(entry)
      const variants = [...card?.variants?.items ?? [], ...card?.variants?.related ?? []].map(entry)
      const derived = (card?.variants?.derived ?? []).map(entry)
      const seen = new Set(members.map(m => m.char))
      const fresh = list => list.filter(item => !seen.has(item.char) && seen.add(item.char))
      return { members, variants: fresh(variants), derived: fresh(derived) }
    })
    // A failed read is asked again next time rather than kept.
    cards.set(char, loaded.catch(error => { cards.delete(char); throw error }))
  }
  return cards.get(char)
}

/** The form a crop is shown as now: its recorded form, or the character it is written as. */
export const formOf = crop => crop?.written_form ?? crop?.written_character ?? crop?.label ?? ''

const pixels = crop => crop.origin === 'corpus' ? { source_revision: crop.source_revision } : { image_sha256: crop.image_sha256 }
const reread = async crop => {
  const record = crop.origin === 'corpus' ? await corpusCharacter(crop.id) : await readCrop(crop.id)
  return crop.origin ? { ...record, origin: crop.origin } : record
}

async function writeForm(crop, form) {
  const corpus = crop.origin === 'corpus'
  const body = corpus
    ? { id: crypto.randomUUID(), identity: crop.id, revision: crop.revision, source_revision: crop.source_revision, form }
    : { id: crypto.randomUUID(), revision: crop.revision, image_sha256: crop.image_sha256, form }
  await request(corpus ? '/atlas/corpus/written-forms' : `/atlas/characters/${encodeURIComponent(crop.id)}/written-form`, body)
  return reread(crop)
}

/**
 * Mark `crop` as `form`, and answer the crop as it now stands.
 *
 * A form that is an encoded member of the crop's own grapheme (仮 and 假, は and 𛂞) is that character,
 * so the crop's identity follows it, as Quick Review's form bar marks a selection; a form recorded
 * before is cleared. Any other form (a variant, a derived shape, an IDS) is recorded as the written
 * form alone, and the crop keeps its character.
 */
export async function setForm(crop, form) {
  const written = crop.written_character ?? crop.label
  const { members } = await listForms(written)
  if (!members.some(member => member.char === form)) return writeForm(crop, form)
  let now = crop
  if (form !== written) {
    await request('/atlas/corrections', { id: crypto.randomUUID(), character: form,
      crops: [{ id: crop.id, revision: crop.revision, ...pixels(crop) }] })
    now = await reread(crop)
  }
  return now.written_form ? writeForm(now, null) : now
}
