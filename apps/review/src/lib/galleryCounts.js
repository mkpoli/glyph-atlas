import { claimedGrapheme, writtenLabel } from './identity.js'
import { styleRank } from './style.js'

// Keeping a gallery's counts true to what a save did, without reading the gallery again.

/** A list's tile as a save left it: the saved record over the tile, less what the record no longer says. */
export function savedTile(tile, record) {
  const { issue: _issue, box_pending: _boxPending, written_character: _written, identity_status: _status, identity_basis: _basis, ...rest } = tile ?? {}
  return { ...rest, ...record }
}

/**
 * What of the counts a save moved, from a crop as it was to as it is: `totals`, when it changed characters
 * or graphemes; `facets`, when its style group alone did, which a gallery is narrowed by; or null. A crop
 * nothing was known of before may have changed either. A list's tile states no grapheme claim, so only a
 * record that does is compared by it.
 */
export function countsMoved(before, after) {
  if (!before || writtenLabel(before) !== writtenLabel(after)) return 'totals'
  if (Object.hasOwn(before, 'grapheme_claim') && claimedGrapheme(before) !== claimedGrapheme(after)) return 'totals'
  return styleRank(before) === styleRank(after) ? null : 'facets'
}

/** The catalogue's counts after a save, taken into what a view shows: an open gallery keeps its own
 *  total, which the catalogue's answer for its search is not. */
export const withSummary = (data, summary, gallery) => ({ ...(data ?? {}), counts: summary.counts, categories: summary.categories, documents: summary.documents,
  ...(gallery ? {} : { total: summary.total, available: summary.available }), reported_count: summary.reported_count, withheld_count: summary.withheld_count })

/** Whether a list of crops read a page at a time has no more to bring: its last page named no next one. A
 *  count says nothing of it, as a save moves the count and not the pages. */
export const listDone = (answered, after) => answered && !after

/** Reads of one thing, of which only the latest begun is taken: `begin` answers whether it still is. */
export function latest() {
  let last = 0
  return () => { const read = ++last; return () => read === last }
}

/** `run` once, `wait` milliseconds after the last call of a burst. */
export function afterBurst(run, wait) {
  let timer
  return Object.assign(() => { clearTimeout(timer); timer = setTimeout(run, wait) }, { cancel: () => clearTimeout(timer) })
}
