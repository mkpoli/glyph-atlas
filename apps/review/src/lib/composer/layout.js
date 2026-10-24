// The learned layout (han-compose `compose_layout.py`): a small network reads two operands' usual
// boxes and shapes and gives both boxes in the em. `layout.json` holds six seeds of it, float16
// weights and float32 biases, averaged.
import { bytes } from './geometry.js'

const half = new Float32Array(1), halfBits = new Uint32Array(half.buffer)
/** An IEEE half-precision number as a float. */
function fromHalf(h) {
  const sign = (h & 0x8000) << 16, exp = (h >> 10) & 0x1f, frac = h & 0x3ff
  if (exp === 0) return (sign ? -1 : 1) * frac * 2 ** -24
  if (exp === 31) halfBits[0] = sign | 0x7f800000 | (frac << 13)
  else halfBits[0] = sign | ((exp + 112) << 23) | (frac << 13)
  return half[0]
}

function matrix(record) {
  const b = bytes(record.data), [rows, cols] = record.shape, out = new Float32Array(rows * cols)
  if (record.scale) {
    const scale = new Float32Array(bytes(record.scale).buffer)
    const q = new Int8Array(b.buffer)
    for (let i = 0; i < out.length; i++) out[i] = q[i] * scale[i % cols]
  } else {
    const view = new DataView(b.buffer)
    for (let i = 0; i < out.length; i++) out[i] = fromHalf(view.getUint16(2 * i, true))
  }
  return { rows, cols, data: out }
}

/** `layout.json` read into networks: per seed, its layers as `{ rows, cols, data }` and biases. */
export function readLayout(json) {
  const seeds = json.seeds.map(run => {
    const layers = []
    for (let k = 0; k < run.length; k += 2) layers.push({ w: matrix(run[k]), b: new Float32Array(bytes(run[k + 1]).buffer) })
    return layers
  })
  // Per operand: 4 mean, 4 spread, count, outline count, then the shape features.
  const shape = (seeds[0][0].w.rows - json.ops.length) / 2 - 10
  return { ops: json.ops, defaults: json.defaults, shape, seeds }
}

const gelu = v => 0.5 * v * (1 + Math.tanh(0.7978845608028654 * (v + 0.044715 * v ** 3)))

/** Both operands' boxes for the features `x`, in font units. */
export function forward(layout, x) {
  const sum = new Float64Array(8)
  for (const layers of layout.seeds) {
    let h = Float32Array.from(x)
    layers.forEach(({ w, b }, k) => {
      const out = new Float32Array(w.cols)
      out.set(b)
      for (let i = 0; i < w.rows; i++) {
        const v = h[i]
        if (!v) continue
        const row = i * w.cols
        for (let j = 0; j < w.cols; j++) out[j] += v * w.data[row + j]
      }
      if (k + 1 < layers.length) for (let j = 0; j < out.length; j++) out[j] = gelu(out[j])
      h = out
    })
    for (let j = 0; j < 8; j++) sum[j] += h[j]
  }
  const p = Array.from(sum, v => v / layout.seeds.length * 1000)
  return [p.slice(0, 4), p.slice(4, 8)]
}

/** The features of two operands under `op` (`compose_layout.features`): `records` are their bucket
 *  records (or null), `places` their places. */
export function features(layout, op, records, places) {
  const out = layout.ops.map(known => known === op ? 1 : 0)
  records.forEach((record, i) => {
    const usual = record?.u?.[places[i]]
    const mean = usual ? usual[0] : layout.defaults[places[i]], spread = usual ? usual[1] : [0, 0, 0, 0], n = usual ? usual[2] : 0
    out.push(...mean.map(v => v / 1000), ...spread.map(v => v / 100), Math.log1p(n) / 5, Math.log1p(record?.o ?? 0) / 3,
      ...(record?.f ?? new Array(layout.shape).fill(0)))
  })
  return out
}
