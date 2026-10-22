// Choosing many tiles of a gallery at once: one at a time, a range in the order they are shown, and the
// tile an arrow key moves to.

/** `selected` with `id` added, or taken out when it was there. */
export function toggled(selected, id) {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** `selected` with every tile of `order` from index `from` to index `to` (either way round) that `can` allows. */
export function withRange(selected, order, from, to, can = () => true) {
  const next = new Set(selected)
  const [start, end] = from < to ? [from, to] : [to, from]
  for (const item of order.slice(Math.max(0, start), end + 1)) if (can(item)) next.add(item.id)
  return next
}

/**
 * The index of the tile an arrow key moves to from the tile at `index`, or null when there is none that
 * way. `boxes` are the tiles' rectangles (`{ left, top, width, height }`) in display order. Left and
 * Right step through the display order. Up and Down go to the nearest row above or below, then to the
 * tile in it whose centre is nearest across. A collage's rows are not aligned, so a tile counts as
 * above or below when its centre is by more than a quarter of the tile's own height, and as in the
 * nearest row when its centre is within half that height of the nearest one's.
 */
export function arrowTarget(boxes, index, key) {
  if (key === 'ArrowLeft') return index > 0 ? index - 1 : null
  if (key === 'ArrowRight') return index < boxes.length - 1 ? index + 1 : null
  if (key !== 'ArrowUp' && key !== 'ArrowDown') return null
  const from = boxes[index]
  if (!from) return null
  const centre = box => [box.left + box.width / 2, box.top + box.height / 2]
  const [x, y] = centre(from), sign = key === 'ArrowDown' ? 1 : -1
  const beyond = boxes.map((box, i) => { const [cx, cy] = centre(box); return { i, dy: (cy - y) * sign, dx: Math.abs(cx - x) } })
    .filter(entry => entry.i !== index && entry.dy > from.height / 4)
  if (!beyond.length) return null
  const nearest = Math.min(...beyond.map(entry => entry.dy))
  return beyond.filter(entry => entry.dy <= nearest + from.height / 2).reduce((a, b) => (b.dx < a.dx ? b : a)).i
}
