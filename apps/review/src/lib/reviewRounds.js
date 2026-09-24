// Full rounds come first; smaller characters follow before any character repeats.
export const MIN_ROUND_SIZE = 6
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
