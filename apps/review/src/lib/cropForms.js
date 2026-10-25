import { request, character as readCrop, corpusCharacter } from './client.js'
import { character as readCard, structure as readStructure } from './layers.js'
import { drawFigure, figuresOf } from './mjFigures.js'

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
// A derived form keeps whether it is encoded and how it was made, which the picker shows.
const derivedEntry = item => ({ ...entry(item), code_point: item.code_point ?? null, encoded: item.encoded, routes: item.routes })

const HAN = /^\p{Script=Han}$/u
// The language whose fonts draw a BabelStone region's shape (U+6236 戶 is drawn as 戸 under `ja`).
const REGION_LANG = { G: 'zh-Hans', H: 'zh-HK', M: 'zh-MO', T: 'zh-Hant', J: 'ja', K: 'ko', P: 'ko-KP', V: 'vi' }
const langOf = regions => [...regions].map(region => REGION_LANG[region]).find(Boolean) ?? ''
const cards = new Map()
/**
 * The forms offered for a crop written as `char`: the members of its grapheme (は, ハ, 𛂞…), then its
 * other forms, as the character's card lists them: the figures the IVD registers for `char` when it
 * registers more than one (所󠄂 and 所󠄃), each named by its MJ figure; the shapes BabelStone gives
 * `char` by region when it gives more than one (所 as ⿰戶斤 in GHTKPV and ⿰戸斤 in J); and the
 * variants the sources attest that a gallery widens to or that one is written for the other. The
 * characters related otherwise (a loan, a homograph, a grapheme this form also writes) follow as
 * `related`, and the derived shapes last. A character the layer does not know offers itself alone.
 */
export function listForms(char) {
  if (!char) return Promise.resolve({ members: [], variants: [], related: [], derived: [] })
  if (!cards.has(char)) {
    // A sequence the table has no card for (ツ + U+309A) offers itself alone.
    const card = readCard(pointOf(char)).catch(error => error?.status === 404 ? null : Promise.reject(error))
    // Figures are offered once their font is loaded, since without it they all look like `char`. Only
    // a Han character has regional shapes. A failed read leaves its forms out of this answer, which is
    // not kept, and the card's forms still come.
    let partial = false
    const registered = figuresOf(char).then(async figures => (figures.length && await drawFigure(figures[0].char), figures))
      .catch(() => { partial = true; return [] })
    const described = HAN.test(char) ? readStructure(char).then(found => found.shapes ?? [], () => { partial = true; return [] }) : Promise.resolve([])
    const loaded = Promise.all([card, registered, described]).then(([card, figures, regional]) => {
      const members = (card?.grapheme?.members ?? [{ char }]).map(entry)
      const shapes = regional.map(({ text, regions }) => ({ char: text, code_point: null, script: '', encoded: false, regions, lang: langOf(regions) }))
      const found = card?.variants ?? {}
      const writers = new Set((found.writers ?? []).map(v => v.char)), writes = new Set((found.writes ?? []).map(v => v.char))
      // The card's order: a form that writes this grapheme first (後: 后), then the others.
      const forms = [...found.items ?? [], ...(found.related ?? []).filter(v => v.written || writers.has(v.char))].filter(v => !writes.has(v.char))
        .sort((a, b) => writers.has(b.char) - writers.has(a.char))
      const variants = [...figures.map(figure => ({ ...entry(figure), mj: figure.mj })), ...shapes, ...forms.map(entry)]
      const related = [...found.items ?? [], ...found.related ?? []].filter(v => !forms.includes(v)).map(entry)
      const derived = (found.derived ?? []).map(derivedEntry)
      const seen = new Set(members.map(m => m.char))
      const fresh = list => list.filter(item => !seen.has(item.char) && seen.add(item.char))
      return { members, variants: fresh(variants), related: fresh(related), derived: fresh(derived) }
    })
    // A failed read is asked again next time rather than kept.
    cards.set(char, loaded.then(found => (partial && cards.delete(char), found), error => { cards.delete(char); throw error }))
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
// same across renames and reviews and the same value chosen again later is a new claim. A claim keeps
// the review it names with its id, so a retry sends the same request.
const submissions = new Map()
const submission = (parts, review = null) => {
  const key = parts.join('\u0000')
  if (!submissions.has(key)) submissions.set(key, { id: crypto.randomUUID(), review })
  const { id, review: named } = submissions.get(key)
  return { id, review: named, answered: () => submissions.delete(key) }
}

/** Claim `form` as the crop's, on the version the reader has; with `form` null take back the reader's
 *  own claim. The crop keeps its character, review and revision. Answers the crop with its form now,
 *  and `replaced`, the value of the reader's own claim the save took back (null for none). */
async function claimForm(crop, form, review = null) {
  // A record read from a listing may not name its version; the crop as it stands does.
  const seen = crop.crop_version ? crop : { ...crop, ...await reread(crop) }
  const { id, review: named, answered } = submission(['form', crop.id, seen.crop_version, form ?? ''], review)
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/form`, { id, crop_version: seen.crop_version, form, ...(named ? { review: named } : {}) })
  } catch (error) { if (error.status) answered(); throw error }
  answered()
  return { crop: { ...seen, form: result.form }, replaced: result.replaced ?? null }
}

/** Name the crop's character, as a review of that crop: a checked crop can be named again. Answers the
 *  crop as it now stands and the review's submission id. */
async function writeCharacter(crop, character) {
  const { id } = submission(['character', crop.id, crop.revision, character])
  if (corpusOf(crop)) await request('/atlas/corpus/reviews', { id, identity: crop.id, revision: crop.revision,
    source_revision: crop.source_revision, verdict: 'wrong', issue: 'character', character })
  else await request('/layers/units/' + encodeURIComponent(crop.id), { id, revision: crop.revision,
    image_sha256: crop.image_sha256, verdict: 'wrong', issue: 'character', character })
  return { crop: await reread(crop), review: id }
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
  // The claim names the review, so the two count as one decision.
  const named = await writeCharacter(crop, form)
  return { ...await claimForm(named.crop, form, named.review), reviewed: true }
}

/**
 * Take back a marking: `after` is the crop as `setForm` left it, `before` as it was, and `replaced` the
 * reader's own form claim the marking replaced, as `setForm` answered it. A crop renamed by the
 * marking is named back, which is a review; the reader's earlier form is claimed again, or, when they
 * had none, their claim is taken back.
 */
export async function restoreForm(after, before, replaced = null) {
  const was = before.written_character ?? before.label
  let now = after, reviewed = false, review = null
  if ((after.written_character ?? after.label) !== was) { ({ crop: now, review } = await writeCharacter(after, was)); reviewed = true }
  // A renaming back is a review, which a claim put back with it names.
  return { crop: (await claimForm(now, replaced, replaced ? review : null)).crop, reviewed }
}

// A selection marked as a form outside its grapheme (an ideographic description sequence) is a batch of
// form picks, each 48 crops: a claim on each crop and no review, as `setForm` marks one crop with such a
// form. The batch names the version of each crop the reader saw, read again for a record that lacks it.
export const FORM_BATCH = 48
const picksOf = async (crops, form) => Promise.all(crops.map(async crop => ({ crop: crop.crop_version ? crop : { ...crop, ...await reread(crop) }, form })))
async function sendPicks(picks) {
  const batches = []
  for (let from = 0; from < picks.length; from += FORM_BATCH) batches.push(picks.slice(from, from + FORM_BATCH))
  // A batch answered gives `{ crop, replaced }` for each crop it changed; one kept while writes are
  // paused goes once they resume.
  const marked = [], kept = []
  for (const part of batches) {
    const { id, answered } = submission(['forms', ...part.map(({ crop, form }) => `${crop.id}@${crop.crop_version}@${form ?? ''}`)])
    let result
    try {
      result = await request('/atlas/forms', { id, crops: part.map(({ crop, form }) => ({ id: crop.id, crop_version: crop.crop_version, form })) })
    } catch (error) {
      if (error?.kept) { kept.push(...part.map(pick => pick.crop)); continue }
      if (error?.status) answered()
      throw Object.assign(error ?? new Error(), { marked, kept })
    }
    answered()
    const byId = new Map(part.map(({ crop }) => [crop.id, crop]))
    for (const { id: target, form, replaced } of result.crops ?? []) if (byId.has(target)) marked.push({ crop: { ...byId.get(target), form }, replaced: replaced ?? null })
  }
  return { marked, kept }
}
/** Mark `crops` as `form`, a batch of picks at a time. Answers `{ marked, kept }`: each crop changed as
 *  it now stands with the value of the reader's own claim the marking replaced, and the crops kept while
 *  writes are paused. A failure carries the same two for the batches sent before it. */
export const markForms = async (crops, form) => sendPicks(await picksOf(crops, form))
/** Take back `markForms`: each crop's replaced value is claimed again, or the reader's claim taken back. */
export const unmarkForms = marked => sendPicks(marked.map(({ crop, replaced }) => ({ crop, form: replaced })))
