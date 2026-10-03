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

/** `U+3042` for あ, `U+1B09E U+3099` for 𛂞 with a voicing mark: the key of the character's card. */
const pointOf = char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
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
    // A sequence the table has no card for (ツ + U+309A) offers itself alone.
    const loaded = readCard(pointOf(char)).catch(error => error?.status === 404 ? null : Promise.reject(error)).then(card => {
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
// same write. A form claim's id is dropped once the site has answered, since the version stays the
// same across renames and reviews and the same value chosen again later is a new claim.
const submissions = new Map()
const submission = (...parts) => {
  const key = parts.join('\u0000')
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  return { id: submissions.get(key), answered: () => submissions.delete(key) }
}

/** Claim `form` as the crop's, on the version the reader has; with `form` null take back the reader's
 *  own claim. The crop keeps its character, review and revision. Answers the crop with its form now,
 *  and `replaced`, the value of the reader's own claim the save took back (null for none). */
async function claimForm(crop, form) {
  // A record read from a listing may not name its version; the crop as it stands does.
  const seen = crop.crop_version ? crop : { ...crop, ...await reread(crop) }
  const { id, answered } = submission('form', crop.id, seen.crop_version, form ?? '')
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/form`, { id, crop_version: seen.crop_version, form })
  } catch (error) { if (error.status) answered(); throw error }
  answered()
  return { crop: { ...seen, form: result.form }, replaced: result.replaced ?? null }
}

/** Name the crop's character, as a review of that crop: a checked crop can be named again. */
async function writeCharacter(crop, character) {
  const { id } = submission('character', crop.id, crop.revision, character)
  if (corpusOf(crop)) await request('/atlas/corpus/reviews', { id, identity: crop.id, revision: crop.revision,
    source_revision: crop.source_revision, verdict: 'wrong', issue: 'character', character })
  else await request('/layers/units/' + encodeURIComponent(crop.id), { id, revision: crop.revision,
    image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character })
  return reread(crop)
}

/**
 * Mark `crop` as `form`, and answer `{ crop, reviewed, replaced }`: the crop as it now stands, whether
 * the marking was itself a review of it, and the value of the reader's own form claim it replaced
 * (null for none), which `restoreForm` puts back.
 *
 * A form that is an encoded member of the crop's own grapheme (仮 and 假, は and 𛂞) is that character,
 * so the crop is reviewed as written with it, and the form is claimed on it. Any other form (a variant,
 * a derived shape, an IDS) is claimed alone, and the crop keeps its character; its own character,
 * chosen, is a confirmation of it. A claim replaces the reader's earlier one on the crop.
 */
export async function setForm(crop, form) {
  const written = crop.written_character ?? crop.label
  const { members } = await listForms(written)
  if (form === written || !members.some(member => member.char === form)) return { ...await claimForm(crop, form), reviewed: false }
  const named = await writeCharacter(crop, form)
  return { ...await claimForm(named, form), reviewed: true }
}

/**
 * Take back a marking: `after` is the crop as `setForm` left it, `before` as it was, and `replaced` the
 * reader's own form claim the marking replaced, as `setForm` answered it. A crop renamed by the
 * marking is named back, which is a review; the reader's earlier form is claimed again, or, when they
 * had none, their claim is taken back.
 */
export async function restoreForm(after, before, replaced = null) {
  const was = before.written_character ?? before.label
  let now = after, reviewed = false
  if ((after.written_character ?? after.label) !== was) { now = await writeCharacter(after, was); reviewed = true }
  return { crop: (await claimForm(now, replaced)).crop, reviewed }
}
