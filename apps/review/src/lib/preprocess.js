// A crop as the classifier takes it, computed as `glyph_atlas.classify.preprocess` and `crop_array`
// compute it with Pillow and NumPy, so the browser's vector for an image is the one the index would
// hold for it: grey on white, padded to a centred square, resized to `size` with Pillow's bilinear
// filter, scaled to [0, 1], normalised and repeated into three channels. Each integer step follows
// Pillow's own fixed-point arithmetic; tests/test_image_search_preprocess.py compares the two.

/** Pillow's alpha composite of one pixel over opaque white, for one channel (libImaging/AlphaComposite.c). */
function overWhite(value, alpha) {
  if (alpha === 255) return value
  if (alpha === 0) return 255
  const bits = 7
  const blend = 255 * (255 - alpha), total = alpha * 255 + blend
  const coef1 = Math.floor(alpha * 255 * 255 * (1 << bits) / total), coef2 = 255 * (1 << bits) - coef1
  const shiftForDiv255 = a => (((a >>> 8) + a) >>> 8)
  return shiftForDiv255(value * coef1 + 255 * coef2 + (0x80 << bits)) >>> bits
}

/** RGBA pixels as Pillow's "L": composited over white, then ITU-R 601-2 luma in fixed point. */
export function grey(rgba, width, height) {
  const out = new Uint8Array(width * height)
  for (let i = 0; i < out.length; i++) {
    const a = rgba[i * 4 + 3]
    const r = overWhite(rgba[i * 4], a), g = overWhite(rgba[i * 4 + 1], a), b = overWhite(rgba[i * 4 + 2], a)
    out[i] = (r * 19595 + g * 38470 + b * 7471 + 0x8000) >>> 16
  }
  return out
}

/** The grey image centred on a white square of its longer side. */
export function square(pixels, width, height, pad = 255) {
  const side = Math.max(width, height), out = new Uint8Array(side * side).fill(pad)
  const left = Math.floor((side - width) / 2), top = Math.floor((side - height) / 2)
  for (let y = 0; y < height; y++) out.set(pixels.subarray(y * width, (y + 1) * width), (top + y) * side + left)
  return { pixels: out, side }
}

const PRECISION_BITS = 32 - 8 - 2

/** Pillow's `precompute_coeffs` and `normalize_coeffs_8bpc` for the bilinear filter (libImaging/Resample.c). */
function coefficients(inSize, outSize) {
  const scale = inSize / outSize, filterScale = Math.max(scale, 1), support = filterScale
  const ksize = Math.ceil(support) * 2 + 1
  const bounds = new Int32Array(outSize * 2), kk = new Int32Array(outSize * ksize)
  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale, ss = 1 / filterScale
    const xmin = Math.max(0, Math.trunc(center - support + 0.5))
    const xmax = Math.min(inSize, Math.trunc(center + support + 0.5)) - xmin
    const weights = []
    let total = 0
    for (let x = 0; x < xmax; x++) {
      let d = Math.abs((x + xmin - center + 0.5) * ss)
      const w = d < 1 ? 1 - d : 0
      weights.push(w); total += w
    }
    for (let x = 0; x < xmax; x++) {
      const w = total !== 0 ? weights[x] / total : weights[x]
      kk[xx * ksize + x] = w < 0 ? Math.trunc(-0.5 + w * (1 << PRECISION_BITS)) : Math.trunc(0.5 + w * (1 << PRECISION_BITS))
    }
    bounds[xx * 2] = xmin; bounds[xx * 2 + 1] = xmax
  }
  return { ksize, bounds, kk }
}

function clip8(value) {
  if (value >= 2 ** (PRECISION_BITS + 8)) return 255
  if (value <= 0) return 0
  return Math.floor(value / 2 ** PRECISION_BITS)
}

/** Pillow's `resize(..., BILINEAR)` of an 8-bit grey square: a horizontal pass, then a vertical one. */
export function resize(pixels, inWidth, inHeight, outWidth, outHeight) {
  if (inWidth === outWidth && inHeight === outHeight) return pixels.slice()
  const horizontal = coefficients(inWidth, outWidth), vertical = coefficients(inHeight, outHeight)
  const first = vertical.bounds[0], last = vertical.bounds[outHeight * 2 - 2] + vertical.bounds[outHeight * 2 - 1]
  let source = pixels, rows = inHeight, offset = 0
  if (inWidth !== outWidth) {
    rows = last - first
    const temp = new Uint8Array(outWidth * rows)
    for (let yy = 0; yy < rows; yy++) {
      const row = (yy + first) * inWidth
      for (let xx = 0; xx < outWidth; xx++) {
        const xmin = horizontal.bounds[xx * 2], xmax = horizontal.bounds[xx * 2 + 1], k = xx * horizontal.ksize
        let ss = 2 ** (PRECISION_BITS - 1)
        for (let x = 0; x < xmax; x++) ss += pixels[row + x + xmin] * horizontal.kk[k + x]
        temp[yy * outWidth + xx] = clip8(ss)
      }
    }
    source = temp; offset = first
  }
  if (inHeight === outHeight) return source
  const out = new Uint8Array(outWidth * outHeight)
  for (let yy = 0; yy < outHeight; yy++) {
    const ymin = vertical.bounds[yy * 2] - offset, ymax = vertical.bounds[yy * 2 + 1], k = yy * vertical.ksize
    for (let xx = 0; xx < outWidth; xx++) {
      let ss = 2 ** (PRECISION_BITS - 1)
      for (let y = 0; y < ymax; y++) ss += source[(y + ymin) * outWidth + xx] * vertical.kk[k + y]
      out[yy * outWidth + xx] = clip8(ss)
    }
  }
  return out
}

/** The grey `size` x `size` square of an RGBA crop, as `classify.preprocess` returns it. */
export function preprocess(rgba, width, height, size = 128) {
  if (width < 1 || height < 1) throw new RangeError('A crop must have a positive size.')
  const { pixels, side } = square(grey(rgba, width, height), width, height)
  return resize(pixels, side, side, size, size)
}

/** The model's input tensor, as `classify.crop_array` builds it in float32. */
export function tensor(squarePixels, size = 128, mean = 0.449, std = 0.226) {
  const out = new Float32Array(3 * size * size), m = Math.fround(mean), s = Math.fround(std), max = Math.fround(255)
  for (let i = 0; i < size * size; i++) {
    const value = Math.fround(Math.fround(Math.fround(squarePixels[i]) / max - m) / s)
    out[i] = value; out[size * size + i] = value; out[2 * size * size + i] = value
  }
  return out
}
