import { request, character as readCrop, corpusCharacter } from './client.js'
import { character as readCard } from './layers.js'

/**
 * What a crop is written as, read and set through the calls the interface uses and nothing else:
 * `listForms` for the forms a crop may be marked as, `setForm` to mark it, and `restoreForm` to take a
 * marking back. A form is a claim in the site's ledger (docs/design/form-model.md): the reader names a
 * value (a character, a character with a variation selector, or an ideographic description sequence),
 * the site files it under that value's form, and the claim rests on the evidence version the reader
 * saw (`crop_version`). A crop's record carries the forms that hold on it now, as `form`.
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

/** The names a crop's form shows: each form's representation, or each competing one when people
 *  disagree; empty for a crop nobody has sorted, or one whose candidates were all rejected. */
export function formNames(form) {
  return (form?.values ?? []).filter(value => value.text).map(value => value.text)
}

/** The form a crop is shown as now: the form the site holds for it, or the character it is written
 *  as; none while people disagree, so that any form chosen is a claim. */
export const formOf = crop => crop?.form?.status === 'disputed' ? ''
  : formNames(crop?.form)[0] ?? crop?.written_character ?? crop?.label ?? ''

const corpusOf = crop => crop.origin === 'corpus'
const reread = async crop => {
  const record = corpusOf(crop) ? await corpusCharacter(crop.id) : await readCrop(crop.id)
  return crop.origin ? { ...record, origin: crop.origin } : record
}

// One submission id per crop, revision or version, and value, so a retry after a lost answer is the
// same write.
const submissions = new Map()
const submission = (...parts) => {
  const key = parts.join('\u0000')
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  return submissions.get(key)
}

/** Claim `form` as the crop's, on the version the reader has; with `form` null take back the reader's
 *  own claim. The crop keeps its character, review and revision. */
async function claimForm(crop, form) {
  // A record read from a listing may not name its version; the crop as it stands does.
  const seen = crop.crop_version ? crop : { ...crop, ...await reread(crop) }
  const id = submission('form', crop.id, seen.crop_version, form ?? '')
  const result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/form`, { id, crop_version: seen.crop_version, form })
  return { ...seen, form: result.form }
}

/** Name the crop's character, as a review of that crop: a checked crop can be named again. */
async function writeCharacter(crop, character) {
  const id = submission('character', crop.id, crop.revision, character)
  if (corpusOf(crop)) await request('/atlas/corpus/reviews', { id, identity: crop.id, revision: crop.revision,
    source_revision: crop.source_revision, verdict: 'wrong', issue: 'character', character })
  else await request('/layers/units/' + encodeURIComponent(crop.id), { id, revision: crop.revision,
    image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character })
  return reread(crop)
}

/**
 * Mark `crop` as `form`, and answer `{ crop, reviewed }`: the crop as it now stands, and whether the
 * marking was itself a review of it.
 *
 * A form that is an encoded member of the crop's own grapheme (仮 and 假, は and 𛂞) is that character,
 * so the crop is reviewed as written with it, and the form is claimed on it. Any other form (a variant,
 * a derived shape, an IDS) is claimed alone, and the crop keeps its character; its own character,
 * chosen, is a confirmation of it. A claim replaces the reader's earlier one on the crop.
 */
export async function setForm(crop, form) {
  const written = crop.written_character ?? crop.label
  const { members } = await listForms(written)
  if (form === written || !members.some(member => member.char === form)) return { crop: await claimForm(crop, form), reviewed: false }
  const named = await writeCharacter(crop, form)
  return { crop: await claimForm(named, form), reviewed: true }
}

/**
 * Take back a marking: `after` is the crop as `setForm` left it, `before` as it was. A crop renamed by
 * the marking is named back, which is a review; the reader's claim is taken back, and the form the
 * crop showed before is claimed again only when it was the reader's own and so went with it.
 */
export async function restoreForm(after, before) {
  const was = before.written_character ?? before.label
  let now = after, reviewed = false
  if ((after.written_character ?? after.label) !== was) { now = await writeCharacter(after, was); reviewed = true }
  try { now = await claimForm(now, null) } catch (error) { if (error.status !== 409) throw error }
  if (formNames(before.form).length && formOf(now) !== formOf(before)) now = await claimForm(now, formOf(before))
  return { crop: now, reviewed }
}
