// What a crop paints before its image arrives: its paper colour, in the rectangle the image will fill.
//
// A crop's record names its paper colour (`tone`) and its image's size in pixels (`image_size`), both
// read from the published image (`glyph_atlas.tone`). The rectangle is drawn as a mask the image's
// own proportions shape, contained and centred in the crop's box as the image itself is
// (`object-fit: contain`), so the box keeps its size and the tone sits exactly under the image. A
// record without a tone is painted in the theme's paper (`--crop-paper`).

const TONE = /^#[0-9a-f]{6}$/i

/** The margin the site's display crops are cut with around their box, as a share of its longer side. */
const MARGIN = 0.08

/**
 * Whether `item`'s crop image was cut with the site's display margin (`atlas.crop_bounds`) rather
 * than to its box alone (a holder's IIIF region or an exact local cut).
 */
export function isPaddedCrop(item, { corpus = false, data = null } = {}) {
  const image = String(item?.image ?? '')
  if (!corpus) return image.startsWith('/atlas/')
  return image.startsWith('/atlas/media/') && data?.crop_mode !== 'remote_iiif' && data?.kind !== 'glyph-rect'
}

/**
 * The rectangle in source pixels that `item`'s crop image was cut from (`atlas.crop_bounds`), so
 * the early crop view can scale it to match the page view and clip it to `box`.
 */
export function cropCut(box, { padded = false, contextBox = null } = {}) {
  if (!box) return null
  if (!padded) return box
  const longer = Math.max(box.w, box.h), pad = MARGIN * longer
  const left = Math.max(0, Math.floor(box.x - pad))
  const top = Math.max(0, Math.floor(box.y - pad))
  let right = Math.floor(box.x + box.w + pad)
  let bottom = Math.floor(box.y + box.h + pad)
  if (contextBox) {
    // `CONTEXT_REACH` is (3, 5): if the context window stopped short of that reach, the page ended there.
    if (contextBox.x + contextBox.w < Math.floor(box.x + box.w + 3 * longer)) right = Math.min(right, contextBox.x + contextBox.w)
    if (contextBox.y + contextBox.h < Math.floor(box.y + box.h + 5 * longer)) bottom = Math.min(bottom, contextBox.y + contextBox.h)
  }
  return { x: left, y: top, w: Math.max(1, right - left), h: Math.max(1, bottom - top) }
}

/**
 * The crop image's width and height, or their proportions: its recorded size, else its box with the
 * margin a display crop of the site's own is cut with (a holder's image is the box itself), else null.
 */
export function cropShape(item) {
  const size = item?.image_size
  if (Array.isArray(size) && size[0] > 0 && size[1] > 0) return [size[0], size[1]]
  const box = item?.box, [sx, sy] = item?.source_scale ?? [1, 1]
  if (!(box?.w > 0 && box?.h > 0)) return null
  const w = box.w * sx, h = box.h * sy
  const margin = String(item.image ?? '').startsWith('/atlas/media/') ? 2 * MARGIN * Math.max(w, h) : 0
  return [w + margin, h + margin]
}

/** The mask that shapes the tone: a rectangle in the crop's proportions, as an image `contain` can fit. */
function mask([w, h]) {
  const r = n => Math.round(n * 100) / 100
  return `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 ${r(w)} ${r(h)}'%3E%3Crect width='${r(w)}' height='${r(h)}'/%3E%3C/svg%3E")`
}

/**
 * The inline style of a crop's box: its tone and the mask of its shape. `shape` overrides the record's,
 * once the image has come and its own size is known. With no shape at all, the tone is a centred square.
 */
export function cropPaint(item, shape = null) {
  return [cropTone(item), `--crop-shape:${mask(shape ?? cropShape(item) ?? [1, 1])}`].filter(Boolean).join(';')
}

/** Only the tone, for a box already in the crop's shape; nothing when the record names none. */
export function cropTone(item) {
  return TONE.test(item?.tone ?? '') ? `--tone:${item.tone}` : ''
}
