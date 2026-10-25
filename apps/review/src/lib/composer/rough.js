// A description laid out roughly, before any drawing data has loaded: each component set in the page
// font in a box its operators give it, in a unit square with y down. ⿰ ⿲ split it into columns,
// ⿱ ⿳ into rows, an enclosure puts its first part in the whole box and its second where the
// composer's `INSIDE` puts a part no host shows, ⿻ and ㇯ lay both parts over the whole box (㇯'s
// second as a cut), ⿾ mirrors and ⿿ turns what it holds. A tree is the editor's (`lib/ids.js`):
// a component is its text, a node `{ op, parts }`, an empty slot null.

// Where an enclosed part sits, as shares of the box (x0, y0, x1, y1), y down: han-compose's INSIDE
// turned over, with ⿼ and ⿽ added as mirrors of ⿷ and ⿸.
const INSIDE = {
  '⿴': [0.2, 0.2, 0.8, 0.85], '⿵': [0.2, 0.2, 0.8, 1], '⿶': [0.2, 0, 0.8, 0.8], '⿷': [0.2, 0.15, 1, 0.85],
  '⿸': [0.3, 0.22, 1, 1], '⿹': [0, 0.22, 0.75, 1], '⿺': [0.3, 0, 1, 0.75], '⿼': [0, 0.15, 0.8, 0.85], '⿽': [0, 0, 0.7, 0.75],
}
const ROWS = { '⿰': [0, 2], '⿲': [0, 3], '⿱': [1, 2], '⿳': [1, 3] }

/**
 * The leaves of `tree` with their boxes: `{ char, box, flip, cut }`, `char` null for an empty slot,
 * `box` [x0, y0, x1, y1] in the unit square, `flip` [x, y] whether its glyph is mirrored in its box.
 */
export function roughLayout(tree, box = [0, 0, 1, 1], flip = [false, false], cut = false, out = []) {
  if (tree === null || typeof tree === 'string') { out.push({ char: tree, box, flip, cut }); return out }
  const { op, parts } = tree
  if (ROWS[op]) {
    const [axis, n] = ROWS[op], span = (box[axis + 2] - box[axis]) / n
    parts.forEach((part, i) => {
      const b = box.slice()
      b[axis] = box[axis] + i * span
      b[axis + 2] = box[axis] + (i + 1) * span
      roughLayout(part, b, flip, cut, out)
    })
  } else if (INSIDE[op]) {
    roughLayout(parts[0], box, flip, cut, out)
    const [a, b, c, d] = INSIDE[op], w = box[2] - box[0], h = box[3] - box[1]
    roughLayout(parts[1], [box[0] + a * w, box[1] + b * h, box[0] + c * w, box[1] + d * h], flip, cut, out)
  } else if (op === '⿾' || op === '⿿') {
    // Mirrored about the box's centre: each leaf's box moves across it and its glyph turns over.
    for (const leaf of roughLayout(parts[0], box, flip, cut)) {
      const [x0, y0, x1, y1] = leaf.box, turned = op === '⿿'
      out.push({ ...leaf, box: [box[0] + box[2] - x1, turned ? box[1] + box[3] - y1 : y0, box[0] + box[2] - x0, turned ? box[1] + box[3] - y0 : y1],
        flip: [!leaf.flip[0], turned ? !leaf.flip[1] : leaf.flip[1]] })
    }
  } else if (op === '㇯') {
    roughLayout(parts[0], box, flip, cut, out)
    roughLayout(parts[1], box, flip, true, out)
  } else parts.forEach(part => roughLayout(part, box, flip, cut, out))
  return out
}
