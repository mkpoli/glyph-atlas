import { request, character as readCrop } from './client.js'
import { character as readCard, ids as readIds } from './layers.js'
import { drawFigure, figuresOf } from './mjFigures.js'
import { sealsLast } from './cardTiers.js'
import { sortForms } from './formOffers.js'
import { compatibility } from '../../../cloudflare/src/representation.ts'

/**
 * What a crop is written as, read and set through the calls the interface uses and nothing else:
 * `listForms` for the forms a crop may be marked as, `setForm` to mark one crop, and `markForms` and
 * `unmarkForms` to claim a form on many crops and take it back. A form is a claim in the site's ledger
 * (docs/design/form-model.md): the reader names a value (a character, a character with a variation
 * selector, or an ideographic description sequence), the site files it under that value's form, and
 * the claim rests on the evidence version the reader saw (`crop_version`). A crop's record carries the forms that hold on it now, as `form`.
 */

/** `U+3042` for あ, `U+1B09E U+3099` for 𛂞 with a voicing mark: the key of the character's card. */
const pointOf = char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
const entry = item => ({ char: item.char, code_point: item.code_point ?? pointOf(item.char), script: item.script ?? '' })
// A derived form keeps whether it is encoded and how it was made, which the picker shows.
const derivedEntry = item => ({ ...entry(item), code_point: item.code_point ?? null, encoded: item.encoded, routes: item.routes })
// A form the grapheme's crops carry has no character of its own: its value, an IDS or a figure, is its
// name, and no character page can be linked.
const carriedEntry = item => ({ char: item.char, code_point: null, script: 'han', lang: '', form: item.form, count: item.count, corpus_count: item.corpus_count })

const HAN = /^\p{Script=Han}$/u
// The language whose fonts draw a BabelStone region's shape (U+6236 戶 is drawn as 戸 under `ja`).
const REGION_LANG = { G: 'zh-Hans', H: 'zh-HK', M: 'zh-MO', T: 'zh-Hant', J: 'ja', K: 'ko', P: 'ko-KP', V: 'vi' }
const langOf = regions => [...regions].map(region => REGION_LANG[region]).find(Boolean) ?? ''
const cards = new Map()
// A description or a figure claimed on a crop, or taken back, changes its grapheme's forms, so the cards
// read here are read again. A character of its own is on its cards whatever is claimed.
export const formClaimed = (...values) => { if (values.some(value => [...value ?? ''].length > 1)) cards.clear() }
/**
 * The forms offered for a crop written as `char`: the members of its grapheme (は, ハ, 𛂞…) but one
 * drawn as `char` itself (𨓜, 逸 encoded again as its J shape), a kanji's small seal characters last
 * among them, then its
 * other forms, as the character's card lists them: the figures the IVD registers for `char` when it
 * registers more than one (所󠄂 and 所󠄃), each named by its MJ figure; the shapes BabelStone gives
 * `char` by region when it gives more than one (所 as ⿰戶斤 in GHTKPV and ⿰戸斤 in J); and the
 * variants the sources attest that a gallery widens to or that one is written for the other. The
 * characters related otherwise (a loan, a homograph, a grapheme this form also writes) follow as
 * `related`, the derived shapes last, and the described forms and figures the grapheme's crops
 * carry with the variants. A character the layer does not know offers itself alone.
 * A compatibility ideograph (說 U+F96F) is offered nowhere but for a crop written as it: its figure
 * is one of the registered sequences of the character it stands for. `alike` are the forms left out
 * because another stands for them (`sortForms`), and `family` every encoded member of the grapheme.
 */
export function listForms(char) {
  if (!char) return Promise.resolve({ members: [], variants: [], related: [], derived: [], alike: [], family: [] })
  if (!cards.has(char)) {
    // A sequence the table has no card for (ツ + U+309A) offers itself alone.
    const card = readCard(pointOf(char)).catch(error => error?.status === 404 ? null : Promise.reject(error))
    // Figures are offered once their font is loaded, since without it they all look like `char`. Only
    // a Han character has regional shapes. A failed read leaves its forms out of this answer, which is
    // not kept, and the card's forms still come.
    let partial = false
    const registered = figuresOf(char).then(async figures => (figures.length && await drawFigure(figures[0].char), figures))
      .catch(() => { partial = true; return [] })
    const described = HAN.test(char) ? readIds(char).then(found => found.shapes ?? [], () => { partial = true; return [] }) : Promise.resolve([])
    const loaded = Promise.all([card, registered, described]).then(([card, allFigures, allShapes]) => {
      const offered = v => !compatibility(v.char) || v.char === char
      const { figures, shapes: regional, alike } = sortForms(char, allFigures, allShapes)
      // A character drawn as the bare character (𦤀, 臭 encoded again as its J shape) is set apart.
      const twins = new Set(alike.chars)
      const twin = v => twins.has(v.char)
      const family = sealsLast(card?.grapheme?.members ?? [{ char }]).filter(offered)
      const members = family.filter(v => !twin(v)).map(entry)
      const figureEntry = figure => ({ ...entry(figure), mj: figure.mj })
      const shapeEntry = ({ text, regions }) => ({ char: text, code_point: null, script: '', encoded: false, regions, lang: langOf(regions) })
      const shapes = regional.map(shapeEntry)
      const found = card?.variants ?? {}
      const writers = new Set((found.writers ?? []).map(v => v.char)), writes = new Set((found.writes ?? []).map(v => v.char))
      // The card's order: a form that writes this grapheme first (後: 后), then the others.
      const forms = [...found.items ?? [], ...(found.related ?? []).filter(v => v.written || writers.has(v.char))].filter(v => !writes.has(v.char) && offered(v) && !twin(v))
        .sort((a, b) => writers.has(b.char) - writers.has(a.char))
      // One drawn as the bare character is among the forms set apart.
      const apart = new Set([...alike.figures.map(figure => figure.char), ...alike.shapes.map(shape => shape.text)])
      const carried = (found.forms ?? []).filter(form => !apart.has(form.char)).map(carriedEntry)
      const variants = [...figures.map(figureEntry), ...shapes, ...forms.map(entry), ...carried]
      const related = [...found.items ?? [], ...found.related ?? []].filter(v => !forms.includes(v) && offered(v) && !twin(v)).map(entry)
      const derived = (found.derived ?? []).map(derivedEntry)
      const seen = new Set(members.map(m => m.char))
      const fresh = list => list.filter(item => !seen.has(item.char) && seen.add(item.char))
      return { members, variants: fresh(variants), related: fresh(related), derived: fresh(derived),
        family: family.map(v => v.char),
        alike: [...alike.figures.map(figureEntry), ...alike.shapes.map(shapeEntry),
          ...[...family, ...found.items ?? [], ...found.related ?? []].filter(twin)
            .filter((v, i, all) => all.findIndex(other => other.char === v.char) === i).map(entry)] }
    })
    // A failed read is asked again next time rather than kept.
    cards.set(char, loaded.then(found => (partial && cards.delete(char), found), error => { cards.delete(char); throw error }))
  }
  return cards.get(char)
}

/** The character a form offered for `char` is a form of where it is one of its grapheme's (仮 and 假, は and 𛂞),
 *  which choosing it names; null for a form that names no character. */
export const baseOf = (form, { family = [] }) => family.includes(form) ? form : null

/** The names a crop's form shows: each form's representation, or each competing one when people
 *  disagree; empty for a crop nobody has sorted, or one whose candidates were all rejected. */
export function formNames(form) {
  return (form?.values ?? []).filter(value => value.text).map(value => value.text)
}

/** What a crop is built as where no form names its shape (`built_as`): its structures `{ structure, label, by }`,
 *  several while people disagree; none once the crop has a form. */
export const builtAs = crop => formNames(crop?.form).length ? [] : crop?.structure?.values ?? []

/** The form a crop is shown as now: the form the site holds for it, or the character it is written
 *  as; none while people disagree or while only its structure is given, so that any form chosen is a claim. */
export const formOf = crop => crop?.form?.status === 'disputed' || builtAs(crop).length ? ''
  : formNames(crop?.form)[0] ?? crop?.written_character ?? crop?.label ?? ''

const reread = async crop => {
  const record = await readCrop(crop.id)
  return crop.origin ? { ...record, origin: crop.origin } : record
}

// One submission id per crop, version and value, so a retry after a lost answer is the same write. It is
// dropped once the site has answered, since the version stays the same across renames and reviews and the
// same value chosen again later is a new claim.
const submissions = new Map()
const submission = parts => {
  const key = parts.join('\u0000')
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  return { id: submissions.get(key), answered: () => submissions.delete(key) }
}

/** Claim `form` as the crop's, on the version the reader has; with `form` null take back the reader's
 *  own claim. The crop keeps its character, review and revision. Answers the crop with its form now,
 *  and `replaced`, the value of the reader's own claim the save took back (null for none). */
async function claimForm(crop, form) {
  const { id, answered } = submission(['form', crop.id, crop.crop_version, form ?? ''])
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/form`, { id, crop_version: crop.crop_version, form })
  } catch (error) { if (error.status) answered(); throw error }
  answered()
  formClaimed(form, result.replaced)
  return { crop: { ...crop, form: result.form }, replaced: result.replaced ?? null }
}

/**
 * Mark `crop` as `form`, a claim alone: the crop keeps its character (a variant, a derived shape, an IDS,
 * its own character, which is a confirmation of it). A claim replaces the reader's earlier one on the
 * crop. A form of another character is claimed by the review that names that character instead
 * (`baseOf`). Answers `{ crop, replaced }`: the crop as it now stands, and the value of the reader's own
 * form claim it replaced (null for none). `{ target }` gives the crop's structure and no form
 * (`setCropStructure`).
 */
export const setForm = (crop, form) => form !== null && typeof form === 'object' ? setCropStructure(crop, form.target) : claimForm(crop, form)

/**
 * Say what 構形 `crop` is built as, its form left for later, or with `target` null take back the reader's
 * own word. `target` names the 構形 as `setStructure` does. The reader's own form on the crop is taken
 * back with it. Answers the crop as it now stands.
 */
async function setCropStructure(crop, target) {
  const { id, answered } = submission(['structure', crop.id, crop.crop_version, JSON.stringify(target)])
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/structure`, { id, crop_version: crop.crop_version, ...target ?? {} })
  } catch (error) { if (error.status) answered(); throw error }
  answered()
  formClaimed(result.replaced_form)
  return { crop: { ...crop, structure: result.structure, form: result.form } }
}

// A form claimed on many crops at once is a batch of form picks, each 48 crops: a claim on each crop and
// no review, as `claimForm` makes on one. The batch names the version of each crop the reader saw, read again for a record that lacks it.
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
      // A kept batch carries its id in the outbox; the same picks made again later are a new submission.
      if (error?.kept) { answered(); kept.push(...part.map(pick => pick.crop)); continue }
      if (error?.status) answered()
      throw Object.assign(error ?? new Error(), { marked, kept })
    }
    answered()
    formClaimed(...part.map(pick => pick.form), ...(result.crops ?? []).map(crop => crop.replaced))
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

// One submission id per form, target and direction, so a retry after a lost answer is the same write.
// A save the site answered drops every id kept for its form: the same filing made again later is a
// new write, whatever became of an earlier one whose answer was lost.
const filed = new Map()
/**
 * File the form written as `form` (a character, a figure or a description, whether or not anyone has
 * chosen it yet) under a 構形, or with `remove` take back the reader's own claim. `target` names the
 * 構形: `{ char }` by a character or description that has it, `{ char, of }` by a regional shape of the
 * character `of`, `{ char, head: true }` by a layout of it, or `{ structure }` by its id. Filing moves the form: the reader's claims of its
 * other 構形 are taken back in the same save. Answers `{ structures, replaced }`: the 構形 the form
 * stands under now with who says so, and those of the claims taken back.
 */
export async function setStructure(form, target, { remove = false } = {}) {
  const key = [form, JSON.stringify(target), remove].join('\u0000')
  if (!filed.has(key)) filed.set(key, crypto.randomUUID())
  try {
    const result = await request(`/atlas/forms/${encodeURIComponent(form)}/structure`, { id: filed.get(key), ...target,
      ...(remove ? { remove: true } : { replace: true }) })
    for (const kept of [...filed.keys()]) if (kept.startsWith(form + '\u0000')) filed.delete(kept)
    return result
  } catch (error) { if (error.status) filed.delete(key); throw error }
}

// One submission id per fold, so a retry after a lost answer is the same write.
const folds = new Map()
/**
 * Fold the 構形 `from` into the 構形 `into`, so the forms of both stand under `into`, or with `into`
 * null take back the reader's own fold of `from`. Each is named as `setStructure` names one. A
 * reader's new fold of a 構形 replaces their earlier one. Answers `{ structure, folds_into }`: where
 * `from` stands now, null for nowhere.
 */
export async function setFold(from, into) {
  const key = JSON.stringify([from, into])
  if (!folds.has(key)) folds.set(key, crypto.randomUUID())
  try {
    const result = await request('/atlas/structures/folds', { id: folds.get(key), from, ...(into ? { into } : { remove: true }) })
    folds.delete(key)
    return result
  } catch (error) { if (error.status) folds.delete(key); throw error }
}
