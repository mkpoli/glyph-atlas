// What the editor's preview shows: the badge on its frame, and the placeholder an empty slot (or a
// part not drawn yet) becomes, in the drawing's 1000-unit box.

/**
 * The badge of a preview: `{ label, params }`, `label` a key under `form.ids.preview.`. `step` is
 * `loading`, `composed`, `final`, `failed` or `capped` (too large to draw); `drawn` is the composer's
 * answer (`compose` in `index.js`). A drawing using an operator the composer does not draw is
 * approximate, with the operators in `params.ops`.
 */
export function badge(step, drawn = null) {
  if (step === 'final') return { label: 'final', params: {} }
  if (step === 'failed') return { label: 'failed', params: {} }
  if (step === 'capped') return { label: 'tooLarge', params: {} }
  if (step === 'composed' && drawn?.approximate?.length) return { label: 'approximate', params: { ops: drawn.approximate.join(' ') } }
  return { label: 'draft', params: {} }
}

/** The face a whole character fills in the drawing's box ([x, y, width, height], y down): the median
 *  box of full ideographs (han-compose's `Composer.face`), where placeholders stand before the layout
 *  has loaded. */
export const FACE = [38.42, 39.94, 928.36, 921.56]

/** The stroke width a placeholder is drawn with where no drawing gives one: the font's at weight 400. */
export const STROKE = 80

/**
 * A placeholder in `box` ([x, y, width, height], y down): a rounded rectangle inset by half a stroke,
 * outlined in dots a stroke wide. `stroke` is the stroke width of the drawing beside it. Answers
 * `{ x, y, width, height, rx, dot, gap, dots }`: the rectangle, the dots' width, their spacing and how
 * many go round.
 */
export function placeholder(box, stroke = STROKE) {
  const [bx, by, bw, bh] = box
  const s = Math.max(24, Math.min(stroke, Math.min(bw, bh) / 5))
  const inset = Math.min(s / 2, bw / 4, bh / 4)
  const width = Math.max(bw - 2 * inset, 1), height = Math.max(bh - 2 * inset, 1)
  const rx = Math.min(Math.min(width, height) * 0.18, 3 * s)
  // As many dots as fit the outline at about a stroke and a bit apart, so they close evenly round it.
  const perimeter = 2 * (width + height) - 8 * rx + 2 * Math.PI * rx
  return { x: bx + inset, y: by + inset, width, height, rx, dot: s * 0.42, gap: s * 1.15, dots: Math.max(4, Math.round(perimeter / (s * 1.15))) }
}
