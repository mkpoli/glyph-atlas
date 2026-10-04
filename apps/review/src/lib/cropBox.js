// A crop's box in two spaces. The API stores and validates a box in page pixels; the context view
// measures source pixels, which are the same rectangle only when the cached image is the page's own size,
// so `source_scale` converts between them. `data` is the crop's record.
const scaleOf = data => data?.source_scale || [1, 1]

/** A page-pixel box in source pixels. */
export function toSource(data, b) {
  const [sx, sy] = scaleOf(data)
  return { x: b.x * sx, y: b.y * sy, w: b.w * sx, h: b.h * sy }
}

/** The context rectangle in page pixels, which is what a drag is bounded by. */
export function pageBounds(data) {
  const c = data.context_box, [sx, sy] = scaleOf(data)
  return { x: c.x / sx, y: c.y / sy, w: c.w / sx, h: c.h / sy }
}

/** Keep a rectangle inside the context, at least two pixels each way. */
export function bounded(data, { x, y, w, h }) {
  const limits = pageBounds(data), right = limits.x + limits.w, bottom = limits.y + limits.h
  // Whole pixels, the position first, so the rounded box never reaches past the page view.
  x = Math.round(Math.max(Math.ceil(limits.x), Math.min(x, right - 2))); y = Math.round(Math.max(Math.ceil(limits.y), Math.min(y, bottom - 2)))
  return { x, y, w: Math.round(Math.max(2, Math.min(w, Math.floor(right) - x))), h: Math.round(Math.max(2, Math.min(h, Math.floor(bottom) - y))) }
}

/** A box the page view drew, in its source pixels, as the crop's page box inside the page view. */
export function fromEdit(data, source, mode = 'resize') {
  const [sx, sy] = scaleOf(data)
  const next = { x: source.x / sx, y: source.y / sy, w: source.w / sx, h: source.h / sy }
  // A move stops at the page view's edge with its size kept; only a resize changes the size.
  if (mode === 'move') {
    const limits = pageBounds(data), w = Math.round(next.w), h = Math.round(next.h)
    next.x = Math.max(limits.x, Math.min(next.x, limits.x + limits.w - w)); next.y = Math.max(limits.y, Math.min(next.y, limits.y + limits.h - h))
  }
  return bounded(data, next)
}

/** The crop's rectangle in page pixels: the one given, else the one it was cut with. */
export function currentBox(data, box = null) {
  if (box) return box
  if (data.box) return data.box
  const b = data.crop_box, [sx, sy] = scaleOf(data)
  return b ? { x: Math.round(b.x / sx), y: Math.round(b.y / sy), w: Math.round(b.w / sx), h: Math.round(b.h / sy) } : null
}

/** Whether a record's box can be redrawn here. */
export const redrawable = data => Boolean(data?.context && data.context_box && data.crop_box && data.crop_editable !== false)
