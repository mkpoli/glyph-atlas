// Full rounds come first; smaller characters follow before any character repeats.
export const MIN_ROUND_SIZE = 6
// Crops a round deals at first.
export const ROUND_BATCH = 48
// Crops each further load adds as the reader scrolls: small enough to arrive quickly and often.
export const MORE_BATCH = 24
// Reference crops of one reading shown alongside a round: this many already-confirmed crops,
// then up to this many more already-seen crops.
export const REFERENCE_LIMIT = 12
export const automaticCategories = categories => categories.filter(c => c.pending >= MIN_ROUND_SIZE)

/** The next character to review: an unvisited full round, then an unvisited smaller one, largest first. */
export function nextCharacter(categories, current, history, seed) {
  const others = categories.filter(c => c.pending > 0 && c.label !== current)
  const visited = new Set(history.map(round => round.reading))
  const fresh = others.filter(c => !visited.has(c.label))
  const full = automaticCategories(fresh)
  if (full.length) return full[seed % full.length].label
  if (fresh.length) return fresh.reduce((best, c) => c.pending > best.pending ? c : best).label
  const pool = automaticCategories(others).length ? automaticCategories(others) : others
  return pool.length ? pool[seed % pool.length].label : null
}

/**
 * The reference strip for one reading: already-confirmed crops, then already-seen crops that
 * were not already counted as confirmed, each tagged with the state it stands for.
 *
 * A crop counted once: a crop that is both checked and seen (a stale `seen` record from before
 * it was confirmed) shows only as confirmed.
 */
export function mergeReferences(checked, seen, limit = REFERENCE_LIMIT) {
  const checkedIds = new Set(checked.map(item => item.id))
  const confirmed = checked.slice(0, limit).map(item => ({ ...item, referenceState: 'checked' }))
  const others = seen.filter(item => !checkedIds.has(item.id)).slice(0, limit)
    .map(item => ({ ...item, referenceState: 'seen' }))
  return [...confirmed, ...others]
}
