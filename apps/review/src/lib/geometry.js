/**
 * Box and pointer arithmetic for the page image.
 *
 * A crop maps page pixels to css pixels with one factor `scale` (css px per page px), so a box
 * `{x, y, w, h}` is drawn at `((x - origin.x) * scale, (y - origin.y) * scale)` inside a
 * `w * scale` by `h * scale` element. `origin` is the box the element shows: the page itself in the
 * page view, one line in the line view.
 */

/** A rectangle in page pixels, normalised so that `w` and `h` are positive. */
export function normalizeBox(a, b) {
  const x = Math.round(Math.min(a.x, b.x))
  const y = Math.round(Math.min(a.y, b.y))
  const w = Math.round(Math.abs(a.x - b.x))
  const h = Math.round(Math.abs(a.y - b.y))
  return { x, y, w, h }
}

/** Whether a box is worth sending: a stray click is not a unit. */
export function isDrawn(box, minimum = 6) {
  return box.w >= minimum && box.h >= minimum
}

/** The pointer position in page pixels, from a mouse or touch event over a crop element. */
export function pagePoint(event, element, origin, scale) {
  const rect = element.getBoundingClientRect()
  const point = event.touches?.[0] ?? event.changedTouches?.[0] ?? event
  return {
    x: origin.x + (point.clientX - rect.left) / scale,
    y: origin.y + (point.clientY - rect.top) / scale,
  }
}

/** Whether a page point is inside a box. */
export function inBox(point, box) {
  return (
    box != null &&
    point.x >= box.x &&
    point.x <= box.x + box.w &&
    point.y >= box.y &&
    point.y <= box.y + box.h
  )
}

/** The union of boxes, or null when there are none. */
export function unionBox(boxes) {
  const present = boxes.filter(Boolean)
  if (!present.length) return null
  const left = Math.min(...present.map((box) => box.x))
  const top = Math.min(...present.map((box) => box.y))
  const right = Math.max(...present.map((box) => box.x + box.w))
  const bottom = Math.max(...present.map((box) => box.y + box.h))
  return { x: left, y: top, w: right - left, h: bottom - top }
}

/** A box grown by `margin` page pixels on every side, kept inside `within` when given. */
export function padBox(box, margin, within = null) {
  let out = {
    x: box.x - margin,
    y: box.y - margin,
    w: box.w + margin * 2,
    h: box.h + margin * 2,
  }
  if (within) {
    const x = Math.max(within.x, out.x)
    const y = Math.max(within.y, out.y)
    const right = Math.min(within.x + within.w, out.x + out.w)
    const bottom = Math.min(within.y + within.h, out.y + out.h)
    out = { x, y, w: Math.max(1, right - x), h: Math.max(1, bottom - y) }
  }
  return out
}

/**
 * Cut a box in two at a point, perpendicular to the line's reading direction: a vertical line is cut
 * by a horizontal line at `point.y`, a horizontal line by a vertical one at `point.x`. A cut that
 * would leave a sliver on either side is refused (null), so that `s` on the edge does nothing.
 */
export function splitBox(box, point, vertical, minimum = 4) {
  if (vertical) {
    const y = Math.round(point.y)
    const top = { x: box.x, y: box.y, w: box.w, h: y - box.y }
    const bottom = { x: box.x, y, w: box.w, h: box.y + box.h - y }
    if (top.h < minimum || bottom.h < minimum) return null
    return [top, bottom]
  }
  const x = Math.round(point.x)
  const left = { x: box.x, y: box.y, w: x - box.x, h: box.h }
  const right = { x, y: box.y, w: box.x + box.w - x, h: box.h }
  if (left.w < minimum || right.w < minimum) return null
  return [left, right]
}

/**
 * The reading order of boxes within a line: down the page for vertical text, across it otherwise,
 * and for two-column 割書 lines down the right column first.
 */
export function orderUnits(units, vertical = true) {
  return [...units].sort((a, b) => {
    if (!a.box || !b.box) return (a.seq ?? 0) - (b.seq ?? 0)
    if (vertical) {
      const column = b.box.x - a.box.x
      if (Math.abs(column) > Math.min(a.box.w, b.box.w) * 0.5) return column
      return a.box.y - b.box.y || a.box.x - b.box.x
    }
    const row = a.box.y - b.box.y
    if (Math.abs(row) > Math.min(a.box.h, b.box.h) * 0.5) return row
    return a.box.x - b.box.x || a.box.y - b.box.y
  })
}

/**
 * The columns of a line whose boxes form more than one vertical run: a 割書 line holds two shorter
 * columns inside one box. Returns one array of units per column, right to left for vertical text.
 * A line whose units form a single run answers one column, so the caller can render either way.
 */
export function columns(units, vertical = true) {
  const ordered = orderUnits(units, vertical)
  const columns = []
  for (const unit of ordered) {
    const column = columns[columns.length - 1]
    const last = column?.[column.length - 1]
    const gap = vertical ? 12 : 12
    if (last && last.box && unit.box) {
      const same = vertical
        ? Math.abs(unit.box.x + unit.box.w / 2 - (last.box.x + last.box.w / 2)) <
          Math.max(unit.box.w, last.box.w) * 0.6 + gap
        : Math.abs(unit.box.y + unit.box.h / 2 - (last.box.y + last.box.h / 2)) <
          Math.max(unit.box.h, last.box.h) * 0.6 + gap
      if (same) {
        column.push(unit)
        continue
      }
      // A box that overlaps the column already open stays in it; a disjoint one starts a new column.
      const overlaps = column.some(
        (other) =>
          other.box &&
          unit.box.x < other.box.x + other.box.w + gap &&
          unit.box.x + unit.box.w + gap > other.box.x &&
          unit.box.y < other.box.y + other.box.h &&
          unit.box.y + unit.box.h > other.box.y,
      )
      if (overlaps) {
        column.push(unit)
        continue
      }
    }
    columns.push([unit])
  }
  return columns.filter((column) => column.length)
}

/**
 * The index window to render of a long line: `{start, end}` into `units`, chosen from the scroll
 * offset of a vertical crop, so that a line of several hundred units draws only what is visible.
 * `offsets` gives the position of each unit along the line in css pixels from the line's start.
 */
export function visibleRange(offsets, scrollTop, viewportHeight, margin = 400) {
  if (!offsets.length) return { start: 0, end: 0 }
  const low = scrollTop - margin
  const high = scrollTop + viewportHeight + margin
  let start = offsets.findIndex((offset) => offset >= low)
  if (start < 0) start = offsets.length - 1
  let end = start
  while (end < offsets.length && offsets[end] <= high) end += 1
  return { start, end }
}
