import { isUnassigned } from './identity.js'

// A batch correction marks many crops as one character, a request per batch, and is undone batch by
// batch. Both are a run of saves made one after another.

// One submission id per batch: the character and each crop at its revision. The same batch sent again
// while its first sending is kept or went unanswered is the same submission, which the site records
// once. A batch the site answered or refused (a 4xx) is done with, and the same crops sent later
// (after an undo, say) are a new submission. After a 5xx the site may have recorded it, so it keeps
// its id.
const submissions = new Map()

/** `crops` cut into batches of `size`, each `{ id, key, crops }` with the submission id its contents name. */
export function correctionBatches(character, crops, size) {
  const batches = []
  for (let from = 0; from < crops.length; from += size) {
    const part = crops.slice(from, from + size)
    const key = [character, ...part.map(crop => crop.id + '@' + crop.revision)].join('\u0000')
    if (!submissions.has(key)) {
      if (submissions.size > 256) submissions.delete(submissions.keys().next().value)
      submissions.set(key, crypto.randomUUID())
    }
    batches.push({ id: submissions.get(key), key, crops: part })
  }
  return batches
}

// "Looks right" confirms each selected crop as the character it is labelled with. It goes as a line
// correction, which names a character for each crop: the site saves the crops that still stand and
// names the rest (`skipped`, with a reason; `unchanged` for one a person already checked), so a crop
// that changed since it was shown is reported and never overwritten.

const single = text => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(text)].length === 1
/** The one written character a tile is labelled with, or null when it has none to confirm. A corpus
 *  glyph is judged by its source's written character, a collection crop by its written form or label.
 *  The site takes one character as the Worker's `writtenCharacter` does, in at most 32 code units: no control, format, surrogate
 *  or separator code point, and no combining mark without a base, which would refuse the whole batch. */
export function confirmedCharacter(item) {
  if (isUnassigned(item)) return null
  const raw = item.origin === 'corpus' ? item.written_character : item.written_character || item.label
  const written = typeof raw === 'string' ? raw.trim() : ''
  if (!written || written.length > 32 || /[\p{Cc}\p{Cf}\p{Cs}\p{Z}]/u.test(written) || /^\p{M}/u.test(written) || !single(written)) return null
  return written
}

/**
 * What "Looks right" does with the tiles `items`: the crops to send, and the rest as `{ id, reason }`:
 * `checked` (a person already confirmed it), `unlabelled` (no single character to confirm) or
 * `unavailable` (a corpus glyph whose image this site may not show).
 */
export function confirmationPlan(items) {
  const crops = [], report = []
  for (const item of items) {
    if (item.origin === 'corpus' && !(item.proxyable && item.image)) report.push({ id: item.id, reason: 'unavailable' })
    else if (!confirmedCharacter(item)) report.push({ id: item.id, reason: 'unlabelled' })
    else if (item.state === 'checked') report.push({ id: item.id, reason: 'checked' })
    else crops.push(item)
  }
  return { crops, report }
}

/**
 * What a selection confirms: each chosen tile, and each crop of the place a chosen pair or trigram
 * shows (`runs`, each `{ id, crops }`), once each, though a tile of the gallery shows one of them too.
 */
export function chosenCrops(tiles, runs, selected) {
  const chosen = [...tiles.filter(item => selected.has(item.id)), ...runs.filter(run => selected.has(run.id)).flatMap(run => run.crops ?? [])]
  return [...new Map(chosen.map(item => [item.id, item])).values()]
}

/** `crops` cut into confirmation batches of `size`, with submission ids as `correctionBatches` gives them. */
export const confirmationBatches = (crops, size) => correctionBatches('\u0001confirm', crops, size)

/** The correction a confirmation batch sends: each crop at its revision and pixels, as its own character. */
export function confirmationBody(batch) {
  return { id: batch.id, line: true, crops: batch.crops.map(item => ({ id: item.id, revision: item.revision, character: confirmedCharacter(item),
    ...(item.origin === 'corpus' ? { source_revision: item.source_revision } : { image_sha256: item.image_sha256 }) })) }
}

/**
 * The answers to confirmation batches (`answered` from `sendEach`) by crop: `saved` maps each confirmed
 * crop's id to `{ revision, state }`; `report` names every other crop the batches carried, as
 * `{ id, reason }`: `checked` for one already confirmed, the site's reason (`changed`, `missing`,
 * `unavailable`, `checked`) for one it skipped, and `flagged` for a crop confirmed but still reported
 * for another issue (its box, say). A crop the site named nowhere is reported `changed`, never dropped.
 */
export function confirmationOutcome(answered) {
  const saved = new Map(), report = []
  for (const { item: batch, result } of answered) {
    // The site names unchanged and skipped crops by the id they were sent with, and answers the crops it
    // saved in the order they were sent, by their public id, which an old id is not.
    const results = [...(result?.results ?? [])]
    const unchanged = new Set(result?.unchanged ?? [])
    const skipped = new Map((result?.skipped ?? []).map(s => [s.id, s.reason]))
    for (const crop of batch.crops) {
      if (unchanged.has(crop.id)) { report.push({ id: crop.id, reason: 'checked' }); continue }
      if (skipped.has(crop.id)) { report.push({ id: crop.id, reason: skipped.get(crop.id) }); continue }
      const done = results.shift()
      if (!done) { report.push({ id: crop.id, reason: 'changed' }); continue }
      saved.set(crop.id, { revision: done.revision, state: done.state })
      if (done.state !== 'checked') report.push({ id: crop.id, reason: 'flagged' })
    }
  }
  return { saved, report }
}

/** A report's crops gathered by reason, in `REASONS` order: `[[reason, ids], …]`. */
export const REASONS = ['changed', 'checked', 'flagged', 'missing', 'unavailable', 'unlabelled']
export function reportByReason(report) {
  return REASONS.map(reason => [reason, report.filter(entry => entry.reason === reason).map(entry => entry.id)]).filter(([, ids]) => ids.length)
}

const forget = batch => { if (submissions.get(batch.key) === batch.id) submissions.delete(batch.key) }

/**
 * Send `batches` from `correctionBatches` with `sendEach`. Each batch answered, and one the site
 * refused with a 4xx, gives up its id; a kept one, one answered with a 5xx, or one whose sending failed
 * before the site answered, keeps it.
 */
export async function sendBatches(batches, send) {
  try {
    const done = await sendEach(batches, send)
    done.answered.forEach(({ item }) => forget(item))
    return done
  } catch (error) {
    error.answered.forEach(({ item }) => forget(item))
    if (error.status >= 400 && error.status < 500) forget(error.item)
    throw error
  }
}

/**
 * The crops in `before` (by id, as they were before a correction) as its undo put them back: each at
 * the revision the site's undo answers in `answered` (from `sendEach`) name for it. Null when an
 * answer names no revision for one of them (an undo the site had already made), so the view reads
 * them again.
 */
export function restoredCrops(before, answered) {
  const revisions = new Map(answered.flatMap(({ result }) => result?.results ?? []).map(r => [r.target_id, r.revision]))
  const restored = {}
  for (const [id, item] of Object.entries(before)) {
    if (!revisions.has(id)) return null
    restored[id] = { ...item, revision: revisions.get(id) }
  }
  return restored
}

/**
 * Send `items` in order, one at a time, with `send`; answers `{ answered: [{ item, result }], kept }`.
 * One kept on this device while writes are paused does not stop the run: the rest are kept after it,
 * in order, and all go once writes resume. Any other refusal stops it, and the error carries what came
 * before it as `answered` and `kept`, and the item refused as `item`.
 */
export async function sendEach(items, send) {
  const answered = [], kept = []
  for (const item of items) {
    try { answered.push({ item, result: await send(item) }) }
    catch (error) {
      if (error?.kept) { kept.push(item); continue }
      throw Object.assign(error ?? new Error(), { answered, kept, item })
    }
  }
  return { answered, kept }
}
