// Flagged crops come first; then full rounds; smaller characters follow before any character repeats.
export const MIN_ROUND_SIZE = 6
// Crops a round loads at a time, first batch and each further one alike.
export const ROUND_BATCH = 48
export const automaticCategories = categories => categories.filter(c => c.due >= MIN_ROUND_SIZE)

/**
 * The next character to review. A character with a flagged crop nobody has seen since it was flagged
 * comes first, the one with most such crops; then an unvisited full round, then an unvisited smaller
 * one, largest first.
 */
export function nextCharacter(categories, current, history, seed) {
  const others = categories.filter(c => c.due > 0 && c.label !== current)
  const flagged = others.filter(c => c.due_flagged > 0)
  if (flagged.length) return flagged.reduce((best, c) => c.due_flagged > best.due_flagged ? c : best).label
  const visited = new Set(history.map(round => round.reading))
  const fresh = others.filter(c => !visited.has(c.label))
  const full = automaticCategories(fresh)
  if (full.length) return full[seed % full.length].label
  if (fresh.length) return fresh.reduce((best, c) => c.due > best.due ? c : best).label
  const pool = automaticCategories(others).length ? automaticCategories(others) : others
  return pool.length ? pool[seed % pool.length].label : null
}
