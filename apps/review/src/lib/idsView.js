// A description as its card shows it (`IdsCard`): read into a tree, coloured by layer and laid out in
// boxes, after ids-input's IdsTree and IdsSequence.
import { arity, isNode, isSequence, layout } from './ids.js'
import { tokens } from './decompose.js'

/** The tree of a description to show, its nodes `{ op, parts }` as `parse` gives them, or null where
 *  `text` is no whole description. A 構形's label can name a component no character has (⿰{18}卩)
 *  and any other character, so each part is taken as written. */
export function readSequence(text) {
  if (!isSequence(text) || /[\p{Cc}\p{Z}]/u.test(text)) return null
  const all = tokens(text)
  let at = 0, short = false
  const read = () => {
    if (at >= all.length) { short = true; return null }
    const token = all[at++], n = arity(token)
    return n ? { op: token, parts: Array.from({ length: n }, read) } : token
  }
  const tree = read()
  return short || at < all.length ? null : tree
}

/** The operators above the deepest part of a tree: 0 for a part. */
export const height = part => isNode(part) ? 1 + Math.max(...part.parts.map(height)) : 0

/** The colour of the layer a part sits in, of one it opens for an operator (`path` the indices down to
 *  it): an operator and its parts share a hue, and an operator inside takes another by its place. */
export function tint(path, operator = false) {
  const layer = operator || !path.length ? path : path.slice(0, -1)
  const hue = layer.length ? [350, 210, 75][layer[0] % 3] + layer.slice(1).reduce((sum, n, i) => sum + (n - .5) * 24 / (i + 1), 0) : 305
  return `light-dark(oklch(72% 0.13 ${hue}), oklch(82% 0.11 ${hue}))`
}

/** How a node's boxes are laid out: as its operator places them, but one under the other where the
 *  surrounding part is itself a node, which the second part would cover. */
export const boxLayout = node => {
  const way = layout(node.op)
  return way === 'stack' || (way === 'enclosure' && isNode(node.parts[0])) ? 'column' : way
}

/** The boxes' size in parts, `{ width, height }`, with room for each node's frame and heading. */
export function boxSize(part) {
  if (!isNode(part)) return { width: 1, height: 1 }
  const inner = part.parts.map(boxSize), way = boxLayout(part)
  const widths = inner.map(size => size.width), heights = inner.map(size => size.height)
  const sum = list => list.reduce((total, n) => total + n, 0)
  const [width, tall] = way === 'row' ? [sum(widths), Math.max(...heights)]
    : way === 'column' ? [Math.max(...widths), sum(heights)]
    : [Math.max(widths[0], widths[1] + 1), Math.max(heights[0], heights[1] + 1)]
  return { width: width + .3, height: tall + .7 }
}
