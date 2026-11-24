import { claimedGrapheme, writtenLabel } from './identity.js'

// Keeping a gallery's counts true to what a save did, without reading the gallery again.

/** Whether a save moved a crop between characters or graphemes, so that counts of either changed: a
 *  crop nothing was known of before may have. A confirmed crop, or one flagged as it stood, has not. */
export const countsMoved = (before, after) => !before || writtenLabel(before) !== writtenLabel(after) || claimedGrapheme(before) !== claimedGrapheme(after)

/** The catalogue's counts after a save, taken into what a view shows: an open gallery keeps its own
 *  total, which the catalogue's answer for its search is not. */
export const withSummary = (data, summary, gallery) => ({ ...(data ?? {}), counts: summary.counts, categories: summary.categories, documents: summary.documents,
  ...(gallery ? {} : { total: summary.total, available: summary.available }), reported_count: summary.reported_count, withheld_count: summary.withheld_count })

/** `run` once, `wait` milliseconds after the last call of a burst. */
export function afterBurst(run, wait) {
  let timer
  return Object.assign(() => { clearTimeout(timer); timer = setTimeout(run, wait) }, { cancel: () => clearTimeout(timer) })
}
