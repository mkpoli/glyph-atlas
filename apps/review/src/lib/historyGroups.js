// A reviewer's run of work reads as one group in the history: rows by the same reviewer, each within
// `gap` of the one before, newest first as the history lists them.
export const GAP = 10 * 60 * 1000
export const who = item => item.reviewer?.user ?? item.reviewer?.name ?? item.actor

export function groupRuns(rows, gap = GAP) {
  const groups = []
  for (const row of rows) {
    const last = groups.at(-1), first = row.items[0], prior = last?.rows.at(-1).items.at(-1)
    if (last && who(prior) === who(first) && Date.parse(prior.at) - Date.parse(first.at) <= gap) last.rows.push(row)
    else groups.push({ id: row.id, rows: [row] })
  }
  return groups.map(group => ({ ...group, items: group.rows.flatMap(row => row.items) }))
}
