// Outlines and masks as the composer reads them (han-compose `compose.py`): parts with their contours
// at weight 400 and the move to the heavy master, boxes as [x0, y0, x1, y1] in font units with y up,
// and masks filled by the nonzero rule at pixel centres. Pure functions over plain arrays.

export const EM = 1000
export const ASCENT = 880
export const EM_BOX = [0, ASCENT - EM, EM, ASCENT]

/** Python's `round`: halves go to the even neighbour. */
export function pyRound(x) {
  const r = Math.round(x)
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
const LOOKUP = new Uint8Array(128)
for (let i = 0; i < B64.length; i++) LOOKUP[B64.charCodeAt(i)] = i

/** Base64 to bytes, without `atob`, so it runs alike in browsers and in Bun. */
export function bytes(text) {
  const clean = text.replace(/=+$/, '')
  const out = new Uint8Array(Math.floor(clean.length * 3 / 4))
  let at = 0
  for (let i = 0; i < clean.length; i += 4) {
    const a = LOOKUP[clean.charCodeAt(i)], b = LOOKUP[clean.charCodeAt(i + 1)]
    const c = LOOKUP[clean.charCodeAt(i + 2)] ?? 0, d = LOOKUP[clean.charCodeAt(i + 3)] ?? 0
    const n = (a << 18) | (b << 12) | (c << 6) | d
    if (at < out.length) out[at++] = (n >> 16) & 255
    if (at < out.length) out[at++] = (n >> 8) & 255
    if (at < out.length) out[at++] = n & 255
  }
  return out
}

const int16 = text => { const b = bytes(text); return new Int16Array(b.buffer, b.byteOffset, b.length >> 1) }

/** Pen commands as `[op, n, implied]`: M, L, or Q with its point count, implied when the contour
 *  closes on an implied point (`Q<n>*`). */
export function commands(text) {
  const out = []
  for (const [, op, n, star] of text.matchAll(/([MLQ])(\d*)(\*?)/g)) out.push([op, op === 'Q' ? Number(n) : 1, star === '*'])
  return out
}

/** A contour's box over all its points, off-curve ones too, as the composer measures it. */
export function pointsBox(points) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (let i = 0; i < points.length; i += 2) {
    const x = points[i], y = points[i + 1]
    if (x < x0) x0 = x
    if (x > x1) x1 = x
    if (y < y0) y0 = y
    if (y > y1) y1 = y
  }
  return [x0, y0, x1, y1]
}

/** A part from contours `{ ops, light, heavy, width }`: its box from the light points. */
export function makePart(contours, box) {
  if (!box) {
    box = [Infinity, Infinity, -Infinity, -Infinity]
    for (const c of contours) {
      const b = pointsBox(c.light)
      box = [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.max(box[2], b[2]), Math.max(box[3], b[3])]
    }
  }
  return { contours, box }
}

/** A part record of the export (`b`, `p`, `w`) read as a part, decoded once. */
export function partOf(record, quant = 4) {
  if (record._part) return record._part
  const width = record.w ?? null
  const contours = record.p.map(([ops, light, delta]) => {
    const lo = int16(light), d = int16(delta)
    const l = new Float64Array(lo.length), h = new Float64Array(lo.length)
    for (let i = 0; i < lo.length; i++) { l[i] = lo[i] / quant; h[i] = (lo[i] + d[i]) / quant }
    return { ops: commands(ops), light: l, heavy: h, width }
  })
  Object.defineProperty(record, '_part', { value: makePart(contours, record.b.slice()), enumerable: false })
  return record._part
}

/** A contour as `['M' | 'L' | 'Q', points]` segments, TrueType's implied on-curve points made explicit
 *  (`compose._segments`); points are [x, y] pairs. */
export function segments(ops, points) {
  const out = []
  let at = 0
  for (const [op, n, implied] of ops) {
    let pts = []
    for (let k = 0; k < n; k++) pts.push([points[2 * (at + k)], points[2 * (at + k) + 1]])
    at += n
    if (op === 'M') out.push(['M', [pts[0]]])
    else if (op === 'L') out.push(['L', [pts[0]]])
    else {
      if (implied) {
        const start = [(pts.at(-1)[0] + pts[0][0]) / 2, (pts.at(-1)[1] + pts[0][1]) / 2]
        out.push(['M', [start]])
        pts = [...pts, start]
      }
      const offs = pts.slice(0, -1), end = pts.at(-1)
      offs.forEach((off, k) => out.push(['Q', [off, k + 1 < offs.length ? [(off[0] + offs[k + 1][0]) / 2, (off[1] + offs[k + 1][1]) / 2] : end]]))
    }
  }
  return out
}

/** Contours flattened into closed polygons (`compose._rings`), each a flat [x, y, …] array. */
export function rings(contours, points, steps = 6) {
  const out = []
  contours.forEach((contour, k) => {
    const ring = []
    let last = null
    for (const [kind, seg] of segments(contour.ops, points[k])) {
      if (kind !== 'Q') { last = seg[0]; ring.push(last[0], last[1]); continue }
      const [off, on] = seg
      for (let s = 1; s <= steps; s++) {
        const t = s / steps, u = 1 - t
        ring.push(u * u * last[0] + 2 * u * t * off[0] + t * t * on[0], u * u * last[1] + 2 * u * t * off[1] + t * t * on[1])
      }
      last = on
    }
    if (ring.length >= 6) out.push(ring)
  })
  return out
}

/**
 * A size × size mask of `box` (y up), row 0 at the top, filled by the nonzero rule at pixel centres
 * (`compose.fill`): a Uint8Array of 0 and 1.
 */
export function fill(polys, size, box) {
  const [x0, y0, x1, y1] = box
  const mask = new Uint8Array(size * size)
  if (!polys.length) return mask
  const dx = (x1 - x0) / size, dy = (y1 - y0) / size
  const delta = new Int32Array(size + 1)
  // Per row, the edges crossing its centre line: each adds its direction to the winding right of it.
  const edges = []
  for (const ring of polys) {
    const n = ring.length / 2
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n
      const ax = ring[2 * i], ay = ring[2 * i + 1], bx = ring[2 * j], by = ring[2 * j + 1]
      if (ay === by) continue
      edges.push(ax, ay, bx, by)
    }
  }
  for (let row = 0; row < size; row++) {
    const y = y1 - (row + 0.5) * dy
    delta.fill(0)
    let any = false
    for (let e = 0; e < edges.length; e += 4) {
      const ay = edges[e + 1], by = edges[e + 3]
      const up = ay <= y && by > y, down = ay > y && by <= y
      if (!up && !down) continue
      const ax = edges[e], bx = edges[e + 2]
      const x = ax + (y - ay) * (bx - ax) / (by - ay)
      // The pixel centres at or left of x: the first one right of it starts the run.
      let k = Math.floor((x - x0) / dx - 0.5) + 1
      if (k < 0) k = 0
      if (k > size) k = size
      // Centres exactly on x count as left of it.
      while (k < size && x0 + (k + 0.5) * dx <= x) k++
      while (k > 0 && x0 + (k - 0.5) * dx > x) k--
      delta[k] += up ? 1 : -1
      any = true
    }
    if (!any) continue
    let w = 0
    const base = row * size
    for (let c = 0; c < size; c++) { w += delta[c]; if (w !== 0) mask[base + c] = 1 }
  }
  return mask
}

/** A mask grown by `by` pixels along rows and columns (`_grown_mask`). */
export function grown(mask, size, by) {
  let m = mask
  for (let s = 0; s < by; s++) {
    const g = m.slice()
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) {
      if (!m[r * size + c]) continue
      if (r > 0) g[(r - 1) * size + c] = 1
      if (r + 1 < size) g[(r + 1) * size + c] = 1
      if (c > 0) g[r * size + c - 1] = 1
      if (c + 1 < size) g[r * size + c + 1] = 1
    }
    m = g
  }
  return m
}

export const count = mask => { let n = 0; for (let i = 0; i < mask.length; i++) n += mask[i]; return n }

/** A part's light ink stretched to fill a size × size square (`_square`). */
export const square = (part, size) => fill(rings(part.contours, part.contours.map(c => c.light), 2), size, part.box)

/** A part's outline length (`Composer.ink` of a part): the polygon through all its light points. */
export function partInk(part) {
  if (part._ink !== undefined) return part._ink
  let total = 0
  for (const c of part.contours) {
    const a = c.light, n = a.length / 2
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n
      total += Math.hypot(a[2 * j] - a[2 * i], a[2 * j + 1] - a[2 * i + 1])
    }
  }
  Object.defineProperty(part, '_ink', { value: total, enumerable: false })
  return total
}

export const area = b => Math.max(b[2] - b[0], 0) * Math.max(b[3] - b[1], 0)
export const union = boxes => [Math.min(...boxes.map(b => b[0])), Math.min(...boxes.map(b => b[1])),
  Math.max(...boxes.map(b => b[2])), Math.max(...boxes.map(b => b[3]))]

/** `box`, drawn within `frame`, carried into `region` in proportion. */
export function mapped(box, frame, region) {
  const sx = (region[2] - region[0]) / Math.max(frame[2] - frame[0], 1)
  const sy = (region[3] - region[1]) / Math.max(frame[3] - frame[1], 1)
  return [region[0] + (box[0] - frame[0]) * sx, region[1] + (box[1] - frame[1]) * sy,
    region[0] + (box[2] - frame[0]) * sx, region[1] + (box[3] - frame[1]) * sy]
}

/** How much a part from `source` changes its proportions in `target`. */
export function stretchOf(source, target) {
  const sx = (target[2] - target[0]) / Math.max(source[2] - source[0], 1)
  const sy = (target[3] - target[1]) / Math.max(source[3] - source[1], 1)
  return Math.max(sx / sy, sy / sx)
}

/** `target` narrowed about its centre so a part from `source` changes its proportions by at most `stretch`. */
export function fitted(source, target, stretch) {
  const sx = (target[2] - target[0]) / Math.max(source[2] - source[0], 1)
  const sy = (target[3] - target[1]) / Math.max(source[3] - source[1], 1)
  let [x0, y0, x1, y1] = target
  if (sx > sy * stretch) {
    const half = sy * stretch * (source[2] - source[0]) / 2, middle = (x0 + x1) / 2
    x0 = middle - half; x1 = middle + half
  } else if (sy > sx * stretch) {
    const half = sx * stretch * (source[3] - source[1]) / 2, middle = (y0 + y1) / 2
    y0 = middle - half; y1 = middle + half
  }
  return [x0, y0, x1, y1]
}

/** `box` scaled by `share` about its centre. */
export function shrunk(box, share) {
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2
  const hw = (box[2] - box[0]) * share / 2, hh = (box[3] - box[1]) * share / 2
  return [cx - hw, cy - hh, cx + hw, cy + hh]
}

/** The share of `box` lying inside `room`. */
export function within(box, room) {
  const w = Math.max(0, Math.min(box[2], room[2]) - Math.max(box[0], room[0]))
  const h = Math.max(0, Math.min(box[3], room[3]) - Math.max(box[1], room[1]))
  return w * h / Math.max((box[2] - box[0]) * (box[3] - box[1]), 1)
}

/** A placed part: `part` drawn into `target` from `source`, moved `weight` of the way to the heavy
 *  master along x and y; `native` is the outline length of the glyph it was drawn in. `flips` mirror
 *  it in turn, each about its point along x, y or both (⿾ ⿿); `cut` marks a part taken away (㇯). */
export const placed = (part, source, target, native, origin, weight = [0, 0]) => ({ part, source, target, weight, native, origin })

export const scaleOf = p => [(p.target[2] - p.target[0]) / Math.max(p.source[2] - p.source[0], 1),
  (p.target[3] - p.target[1]) / Math.max(p.source[3] - p.source[1], 1)]

/** A placed part's target box as drawn, after its `flips`. */
export function drawnBox(p) {
  let [x0, y0, x1, y1] = p.target
  for (const f of p.flips ?? []) {
    if (f.x) [x0, x1] = [2 * f.about[0] - x1, 2 * f.about[0] - x0]
    if (f.y) [y0, y1] = [2 * f.about[1] - y1, 2 * f.about[1] - y0]
  }
  return [x0, y0, x1, y1]
}

/** A placed part's contours as drawn: flat [x, y, …] arrays in font units, y up. */
export function drawnContours(p) {
  const [sx, sy] = scaleOf(p)
  const cx = (p.source[0] + p.source[2]) / 2, cy = (p.source[1] + p.source[3]) / 2
  const gx = (p.target[0] + p.target[2]) / 2, gy = (p.target[1] + p.target[3]) / 2
  const [wx, wy] = p.weight
  return p.part.contours.map(c => {
    const out = new Float64Array(c.light.length)
    for (let i = 0; i < out.length; i += 2) {
      let x = (c.light[i] + (c.heavy[i] - c.light[i]) * wx - cx) * sx + gx
      let y = (c.light[i + 1] + (c.heavy[i + 1] - c.light[i + 1]) * wy - cy) * sy + gy
      for (const f of p.flips ?? []) {
        if (f.x) x = 2 * f.about[0] - x
        if (f.y) y = 2 * f.about[1] - y
      }
      out[i] = x; out[i + 1] = y
    }
    return out
  })
}

/** The rings of a group of placed parts, for masks. */
export const groupRings = (group, steps = 6) => group.flatMap(p => rings(p.part.contours, drawnContours(p), steps))

/** A size × size mask of the em, the parts filled and joined (`compose.raster`). */
export function raster(group, size = 96) {
  const mask = new Uint8Array(size * size)
  for (const p of group) {
    const m = fill(rings(p.part.contours, drawnContours(p)), size, EM_BOX)
    for (let i = 0; i < m.length; i++) mask[i] |= m[i]
  }
  return mask
}

const num = v => { const s = v.toFixed(1); return s === '-0.0' ? '0.0' : s }

/** Placed parts as one SVG path in a 1000 × 1000 box, y down from the em's top (`compose.svg_path`);
 *  fill it with the nonzero rule. */
export function svgPath(group) {
  const out = []
  for (const p of group) {
    const drawn = drawnContours(p)
    p.part.contours.forEach((contour, k) => {
      for (const [kind, pts] of segments(contour.ops, drawn[k])) out.push(kind + pts.map(q => `${num(q[0])} ${num(ASCENT - q[1])}`).join(' '))
      out.push('Z')
    })
  }
  return out.join('')
}

/** An SVG path of M, L, Q and Z commands (absolute, as `svgPath` writes it) read back as rings in
 *  font units, y up, its curves flattened in `steps`. */
export function pathRings(d, steps = 6) {
  const out = []
  let ring = [], last = null
  const close = () => { if (ring.length >= 6) out.push(ring); ring = [] }
  for (const [, kind, args] of d.matchAll(/([MLQZ])([^MLQZ]*)/g)) {
    const v = args.trim() ? args.trim().split(/[\s,]+/).map(Number) : []
    if (kind === 'Z') { close(); continue }
    if (kind === 'M') { close(); last = [v[0], ASCENT - v[1]]; ring.push(...last); continue }
    if (kind === 'L') { last = [v[0], ASCENT - v[1]]; ring.push(...last); continue }
    const off = [v[0], ASCENT - v[1]], on = [v[2], ASCENT - v[3]]
    for (let s = 1; s <= steps; s++) {
      const t = s / steps, u = 1 - t
      ring.push(u * u * last[0] + 2 * u * t * off[0] + t * t * on[0], u * u * last[1] + 2 * u * t * off[1] + t * t * on[1])
    }
    last = on
  }
  close()
  return out
}

/** The mask of an SVG path over the em at `size` px. */
export const pathMask = (d, size = 96) => fill(pathRings(d), size, EM_BOX)

/** Overlap of two masks over their union; 1 when both are empty. */
export function iou(a, b) {
  let both = 0, either = 0
  for (let i = 0; i < a.length; i++) { both += a[i] & b[i]; either += a[i] | b[i] }
  return either ? both / either : 1
}
