// Rare characters remain available through an explicit choice.
export const MIN_ROUND_SIZE = 6
export const automaticCategories = categories => categories.filter(c => c.pending >= MIN_ROUND_SIZE)

export function nextCharacter(categories, current, history, seed) {
  const others = automaticCategories(categories).filter(c => c.label !== current)
  const visited = new Set(history.map(round => round.reading))
  const fresh = others.filter(c => !visited.has(c.label))
  const pool = fresh.length ? fresh : others
  return pool.length ? pool[seed % pool.length].label : null
}
