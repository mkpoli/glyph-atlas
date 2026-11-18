import { confirmedCharacter } from './corrections.js'
import { isUnassigned } from './identity.js'

/** Whether a single crop in a sequence occurrence can be confirmed as currently labelled. */
export function canApproveCrop(crop) {
  if (!crop || crop.needs_segmentation || !confirmedCharacter(crop)) return false
  return crop.origin !== 'corpus' || (Boolean(crop.proxyable) && Boolean(crop.image))
}

/** Aggregate review state across the crops of one occurrence (`'checked'`, `'flagged'`, or `'pending'`). */
export function occurrenceState(crops) {
  if (!crops?.length) return 'pending'
  if (crops.every(crop => crop.state === 'checked')) return 'checked'
  if (crops.some(crop => crop.state === 'flagged' || crop.state === 'hard')) return 'flagged'
  return 'pending'
}

/** The written form of each crop in an occurrence and their joined text, or null when no crops are given. */
export function occurrenceWritten(crops, fallback = '') {
  if (!crops?.length) return null
  const forms = crops.map(crop => ({
    text: crop.written_character ?? crop.label ?? '',
    transcribed: isUnassigned(crop),
  }))
  const written = forms.map(form => form.text).join('')
  return { forms, written: written || fallback }
}

/** Merge an updated crop record or patch onto a run's crop, keeping run-only geometry and dating. */
export function mergeCropPatch(crop, patch) {
  if (!patch) return crop
  const merged = { ...crop, ...patch }
  if (crop.crop_box) merged.crop_box = crop.crop_box
  if (crop.dating && !patch.dating) merged.dating = crop.dating
  return merged
}

/** Build the single-crop confirmation request for either a corpus glyph or a collection crop. */
export function approveCropRequest(crop, submissionId) {
  if (crop.origin === 'corpus') {
    return {
      route: '/atlas/corpus/reviews',
      body: {
        id: submissionId,
        identity: crop.id,
        revision: crop.revision,
        source_revision: crop.source_revision,
        verdict: 'match',
        issue: null,
      },
    }
  }
  return {
    route: '/atlas/characters/' + encodeURIComponent(crop.id),
    body: {
      id: submissionId,
      revision: crop.revision,
      image_sha256: crop.image_sha256,
      verdict: 'match',
      issue: null,
    },
  }
}

/** Extract the updated fields from a single-crop confirmation response. */
export function approvedCropPatch(crop, result) {
  if (crop.origin === 'corpus') {
    const { event, ...rest } = result ?? {}
    return {
      ...rest,
      revision: event?.revision ?? rest.revision ?? crop.revision + 1,
      state: event?.state ?? rest.state ?? 'checked',
      issue: null,
    }
  }
  const saved = result?.results?.[0]
  return {
    state: saved?.state ?? 'checked',
    revision: saved?.revision ?? crop.revision + 1,
    issue: null,
  }
}

/** Resolve an updated crop payload after the shared inspector saves or modifies a field. */
export async function resolveInspectedCrop(existing, id, result, how, readCharacter) {
  if (!result || !existing) return null
  if (how === 'changed' || result?.origin === 'corpus') {
    if (!result.event) return result
    const { event, ...rest } = result
    return { ...rest, revision: event.revision ?? rest.revision, state: event.state ?? rest.state }
  }
  try {
    return await readCharacter(id)
  } catch {
    const saved = result?.results?.[0]
    return saved ? { state: saved.state, revision: saved.revision } : null
  }
}
