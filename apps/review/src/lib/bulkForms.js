import { cardTargets, sealsLast } from './cardTiers.js'
import { confirmedCharacter, correctionBatches } from './corrections.js'
import { cropPixels, isUnassigned } from './identity.js'
import { isDescription } from './ids.js'
import { isFigure } from './mjFigures.js'

// Bulk form-and-confirm in Explore: choosing a character's forms (members, IVD figures, regional shapes,
// attested variants and linked IDS/IVS) and marking a selection with both a review confirmation/relabel
// and a `has_form` claim, following the single-crop inspector's identity-and-form rule.

const writtenOf = item => isUnassigned(item) ? '' : (item.written_character || item.label || '')
const graphemeCodeOf = item => item?.grapheme?.code_point ?? (typeof item?.grapheme === 'string' ? item.grapheme : null)

/**
 * The form chips offered in Explore's bulk bar on a character or grapheme page: the character's
 * grapheme members and variants as `listForms` gives them (including IVD figures, regional shapes,
 * attested variants and linked IDS/IVS, with small seal characters last), falling back to
 * `cardTargets(card)` while `listForms` loads or when it fails. A form with no grapheme of its own
 * (`card.variants.own === false`) keeps the graphemes it writes ahead of itself.
 */
export function bulkFormChoices(listed, card = null) {
  if (!listed) return card ? cardTargets(card) : []
  const writes = card?.variants?.own === false ? (card.variants.writes ?? []) : []
  const seen = new Set()
  const unique = [...writes, ...(listed.members ?? []), ...(listed.variants ?? [])]
    .filter(item => item?.char && !seen.has(item.char) && seen.add(item.char))
  return sealsLast(unique)
}

/**
 * What applying `target` does to each selected crop in `chosen`.
 *
 * - A finer form (an IDS, a regional shape or an IVD variation sequence) is always claimed as `has_form`
 *   while each crop keeps its own character and is confirmed (`checked`) when it has one and is not yet
 *   checked.
 * - An encoded member of the crop's grapheme (including the crop's own character, or a member assigned to
 *   an unassigned crop of the same grapheme) both claims `has_form` and confirms or relabels the crop to
 *   `target`.
 * - An attested variant chosen from the form chips (`fromForm`) that lies outside the crop's grapheme is
 *   claimed as `has_form` while the crop keeps its own character and is confirmed (`checked`), matching
 *   the single-crop inspector's `setForm`.
 * - Any other character searched in the box (`!fromForm`) relabels the crop to `target` without claiming
 *   a fine form, as a wrong-character correction.
 */
export function bulkActionPlan({ chosen, target, fromForm = false, families = new Map(), picked = null, pickedFamily = null }) {
  const finer = isDescription(target) || isFigure(target)
  const pickedGrapheme = graphemeCodeOf(picked)
  const fallbackFamily = pickedFamily ?? new Set((picked?.grapheme?.members ?? []).map(member => member.char))

  return chosen.map(item => {
    const written = writtenOf(item)
    const ownFamily = written
      ? (families.get(written) ?? new Set([written]))
      : (pickedGrapheme && graphemeCodeOf(item) === pickedGrapheme ? fallbackFamily : new Set())
    const inFamily = !finer && (written === target || ownFamily.has(target))

    if (finer || (!inFamily && fromForm)) {
      const reviewCharacter = confirmedCharacter(item)
      return {
        item,
        reviewCharacter,
        needsReview: Boolean(reviewCharacter) && item.state !== 'checked',
        relabel: false,
        claimForm: true,
      }
    }
    if (inFamily) {
      return {
        item,
        reviewCharacter: target,
        needsReview: !(written === target && item.state === 'checked'),
        relabel: written !== target,
        claimForm: true,
      }
    }
    return {
      item,
      reviewCharacter: target,
      needsReview: !(written === target && item.state === 'checked'),
      relabel: written !== target,
      claimForm: false,
    }
  })
}

/** Cut `entries` (from `bulkActionPlan`, filtered to `needsReview`) into batches of `size` for `/atlas/corrections`. */
export function bulkReviewBatches(entries, target, fromForm, size) {
  const keyed = entries.map(entry => ({ ...entry, id: entry.item.id, revision: entry.item.revision }))
  return correctionBatches(`\u0001bulk:${fromForm ? 'form' : 'char'}:${target}`, keyed, size)
}

/** The `line: true` request body for one batch from `bulkReviewBatches`. */
export function bulkReviewBody(batch) {
  return {
    id: batch.id,
    line: true,
    crops: batch.crops.map(entry => ({
      id: entry.item.id,
      revision: entry.item.revision,
      character: entry.reviewCharacter,
      ...cropPixels(entry.item),
    })),
  }
}

/**
 * Parse answered `line: true` review batches into:
 * - `saved`: Map of crop id -> `{ revision, state }` for crops written by the server;
 * - `unchanged`: Set of crop ids the server reported already checked as their target character;
 * - `skipped`: Map of crop id -> reason (`changed`, `missing`, `unavailable`, `checked`) for refused crops;
 * - `flagged`: Array of `{ id, reason: 'flagged' }` for confirmed crops still open for another issue;
 * - `batches`: Array of batch ids that wrote at least one crop (and therefore can be undone).
 */
export function bulkReviewOutcome(answered) {
  const saved = new Map(), unchanged = new Set(), skipped = new Map(), flagged = [], batches = []
  for (const { item: batch, result } of answered) {
    const results = [...(result?.results ?? [])]
    const byId = results.length && results.every(entry => entry?.target_id || entry?.id)
      ? new Map(results.map(entry => [entry.target_id ?? entry.id, entry]))
      : null
    const kept = new Set(result?.unchanged ?? [])
    const refused = new Map((result?.skipped ?? []).map(entry => [entry.id, entry.reason]))
    let wrote = 0
    for (const entry of batch.crops) {
      if (kept.has(entry.id)) { unchanged.add(entry.id); continue }
      if (refused.has(entry.id)) { skipped.set(entry.id, refused.get(entry.id)); continue }
      const done = byId ? byId.get(entry.id) : results.shift()
      if (!done) { skipped.set(entry.id, 'changed'); continue }
      wrote += 1
      saved.set(entry.id, { revision: done.revision, state: done.state })
      if (done.state !== 'checked') flagged.push({ id: entry.id, reason: 'flagged' })
    }
    if (wrote > 0) batches.push(batch.id)
  }
  return { saved, unchanged, skipped, flagged, batches }
}

/**
 * Combine a retry's bulk-done record with the previous half of the same operation when a form claim
 * (or second review batch) is retried on overlapping crops for the same target, so Undo still reverts
 * the earlier review batches alongside the newly saved form claims.
 */
export function mergeBulkDone(previous, next, changedIds) {
  const carry = previous?.kind === 'correct' && previous.char === next.char && changedIds.some(id => id in (previous.before ?? {}))
    ? previous
    : null
  if (!carry) return next
  const before = { ...next.before, ...carry.before }
  const reviewBefore = { ...next.reviewBefore, ...carry.reviewBefore }
  const seenDescribed = new Set((carry.described ?? []).map(entry => entry.crop.id))
  const described = [...(carry.described ?? []), ...(next.described ?? []).filter(entry => !seenDescribed.has(entry.crop.id))]
  return {
    ...next,
    batches: [...new Set([...(carry.batches ?? []), ...(next.batches ?? [])])],
    described,
    before,
    reviewBefore,
    count: Object.keys(before).length,
    kept: (carry.kept ?? 0) + (next.kept ?? 0),
  }
}

/**
 * Combine review-restored tiles with form-undo outcomes, preserving the live tile's current form
 * for any crop whose form claim was not yet reverted.
 */
export function mergeUndoTiles({ restored, unmarked, formTiles, stillClaimed, live }) {
  const formById = new Map(unmarked.map(({ crop }) => [crop.id, crop.form]))
  const liveById = new Map(live.map(item => [item.id, item]))
  const merged = { ...formTiles, ...restored }
  for (const [id, tile] of Object.entries(merged)) {
    if (formById.has(id)) merged[id] = { ...tile, form: formById.get(id) }
    else if (stillClaimed.has(id) && liveById.has(id)) merged[id] = { ...tile, form: liveById.get(id).form }
  }
  return merged
}
