// Ideographic description sequences as the form picker reads and writes them, by the rules of the
// Worker's `representation.ts` and `glyph_atlas.representation`: each operator takes its own number of
// descriptions, and a component is an ideograph, a radical, a stroke, a katakana letter standing for a
// component of that shape (コ, マ), a private-use character or ？ for a part no character names, with
// an optional variation selector.

/** The operators, each with how many descriptions it takes, in the order a keyboard row offers them. */
export const OPERATORS = [
  ['⿰', 2], ['⿱', 2], ['⿲', 3], ['⿳', 3], ['⿴', 2], ['⿵', 2], ['⿶', 2], ['⿷', 2], ['⿸', 2], ['⿹', 2],
  ['⿺', 2], ['⿻', 2], ['⿼', 2], ['⿽', 2], ['⿾', 1], ['⿿', 1], ['㇯', 2], ['〾', 1],
]
const ARITY = new Map(OPERATORS)
/** The key that puts each operator in the editor, as the number row and the row under it run: 1–9, 0,
 *  - and = for ⿰ to ⿻, then q w e r t y for ⿼ ⿽ ⿾ ⿿ ㇯ 〾. */
export const KEYS = Object.fromEntries([...'1234567890-=qwerty'].map((key, i) => [key, OPERATORS[i][0]]))
/** The longest description, in code points. */
export const LONGEST = 64
const COMPONENTS = [[0x2E80, 0x2FDF], [0x30A1, 0x30FA], [0x31C0, 0x31EE], [0x3400, 0x4DBF], [0x4E00, 0x9FFF],
  [0xE000, 0xF8FF], [0xF900, 0xFAFF], [0xFF1F, 0xFF1F], [0x20000, 0x3FFFD], [0xF0000, 0x10FFFD]]
/** The part no character names. */
export const UNKNOWN = '？'

const isSelector = char => char !== undefined && /^[︀-️\u{E0100}-\u{E01EF}]$/u.test(char)
export const isComponent = char => {
  const point = char?.codePointAt(0)
  return point !== undefined && COMPONENTS.some(([low, high]) => point >= low && point <= high)
}
export const arity = char => ARITY.get(char) ?? 0

/** Whether `text` is written as a description, which starts with an operator. */
export const isSequence = text => arity([...text ?? ''][0]) > 0

/**
 * A description as a tree: a component is its text (with its selector), a node `{ op, parts }`.
 * Answers `{ tree }`, or `{ problem }` naming what is wrong as `representation.ts` does:
 * `component` (a part no description may name), `missing` (an operator short of descriptions),
 * `extra` (more than its operators take) or `character` (empty, too long, or no operator first).
 *
 * With `open`, as the editor reads a draft, a slot still to fill is `null`: ？ and every part an
 * operator is still missing at the end read as one, an empty text is the empty tree, and a lone
 * component stands as a tree of its own (⿰亻 is ⿰亻 and an empty slot, ⿰？木 an empty slot and 木).
 */
export function parse(text, { open = false } = {}) {
  const chars = [...text ?? '']
  if (open && !chars.length) return { tree: null }
  if (!chars.length || chars.length > LONGEST || (!open && !isSequence(text)) || /[\p{Cc}\p{Cf}\p{Z}]/u.test(text)) return { problem: 'character' }
  let at = 0
  const read = () => {
    const char = chars[at++]
    if (char === undefined) { if (open) return null; throw 'missing' }
    const n = arity(char)
    if (!n) {
      if (!isComponent(char)) throw 'component'
      if (open && char === UNKNOWN) return null
      return isSelector(chars[at]) ? char + chars[at++] : char
    }
    return { op: char, parts: Array.from({ length: n }, read) }
  }
  try {
    const tree = read()
    return at < chars.length ? { problem: 'extra' } : { tree }
  } catch (problem) { return { problem } }
}

/**
 * The ASCII names zi.tools gives the operators, so a sequence can be typed without an IME: LR ⿰,
 * UD ⿱, LL ⿲ (left, middle, right), UU ⿳ (above, middle, below), OC ⿴ (closed), OD ⿵, OU ⿶, OR ⿷
 * and OL ⿼ (open down, up, right, left), RD ⿸, LD ⿹, RU ⿺ and LU ⿽ (where the inner part sits:
 * right and down, left and down, right and up, left and up), XX ⿻ (overlaid), MI ⿾ (mirrored),
 * RO ⿿ (rotated) and SU ㇯ (subtracted). 〾 has none. Source: zi.tools, its IDS search
 * (https://zi.tools/?secondary=ids, the `action_abbr_map` of its page script, read 2026-10-24).
 */
export const ALIASES = {
  LR: '⿰', UD: '⿱', LL: '⿲', UU: '⿳', OC: '⿴', OD: '⿵', OU: '⿶', OR: '⿷', RD: '⿸', LD: '⿹', RU: '⿺',
  XX: '⿻', OL: '⿼', LU: '⿽', MI: '⿾', RO: '⿿', SU: '㇯',
}
const ALIAS = new RegExp(Object.keys(ALIASES).join('|'), 'g')
/** `text` with every ASCII operator name in it written as its operator, read from the left (LLR is ⿲R). */
export const unalias = text => (text ?? '').replace(ALIAS, name => ALIASES[name])

/** Where the description starting at `chars[at]` ends, or -1 when none well-formed starts there: a
 *  text may run on past it (⿰亻哥 in ⿰亻哥と). */
export function descriptionEnd(chars, at = 0) {
  const char = chars[at]
  if (char === undefined) return -1
  const n = arity(char)
  if (!n) return isComponent(char) ? (isSelector(chars[at + 1]) ? at + 2 : at + 1) : -1
  let end = at + 1
  for (let k = 0; k < n && end >= 0; k++) end = descriptionEnd(chars, end)
  return end
}

/** Whether `text` is one well-formed description. */
export const isDescription = text => isSequence(text) && !parse(text).problem

/** Whether `part` is a node, an operator with its parts. */
export const isNode = part => part !== null && typeof part === 'object'

/** A tree written back as a description, an empty slot as ？. */
export const write = tree => tree === null ? UNKNOWN : isNode(tree) ? tree.op + tree.parts.map(write).join('') : tree

/** Every component of a tree with its path (the part indices down to it), in reading order; an empty
 *  slot has the `char` null. */
export function leaves(tree, path = []) {
  return isNode(tree) ? tree.parts.flatMap((part, i) => leaves(part, [...path, i])) : [{ path, char: tree }]
}

/** The path of every part of a tree, nodes and slots, the tree first, in reading order: the order Tab
 *  steps through them. */
export function paths(tree, path = []) {
  return [path, ...(isNode(tree) ? tree.parts.flatMap((part, i) => paths(part, [...path, i])) : [])]
}

/** Whether two paths name the same part. */
export const samePath = (a, b) => Boolean(a && b) && a.length === b.length && a.every((x, i) => x === b[i])

/** The first empty slot after the path `from` in reading order, coming round to the start; the first
 *  of all without `from`. Null when every slot is filled. */
export function nextEmpty(tree, from = null) {
  const all = paths(tree), at = from ? all.findIndex(path => samePath(path, from)) : -1
  for (let k = 1; k <= all.length; k++) {
    const path = all[(at + k) % all.length]
    if (partAt(tree, path) === null) return path
  }
  return null
}

/** `tree` with the part at `path` replaced by `part` (a component's text, a tree, or null). */
export function replaceAt(tree, path, part) {
  if (!path.length) return part
  const [head, ...rest] = path
  return { op: tree.op, parts: tree.parts.map((p, i) => i === head ? replaceAt(p, rest, part) : p) }
}

/** The part at `path`, undefined where the tree has none. */
export const partAt = (tree, path) => path.reduce((node, i) => isNode(node) ? node.parts[i] : undefined, tree)

/** `node` under `op`: the node first and an empty slot for every other part the operator takes, so a
 *  component can be split in place (⿰ round 失 gives ⿰失？). */
export const wrap = (op, node) => ({ op, parts: [node, ...Array(arity(op) - 1).fill(null)] })

/** `tree` with the part at `path` emptied. */
export const clear = (tree, path) => replaceAt(tree, path, null)

/** `tree` with the node at `path` taken away, one level: its first part that is not empty stands in
 *  its place (⿰？木 gives 木), or an empty slot where all are. A component is left as it is. */
export function unwrap(tree, path) {
  const node = partAt(tree, path)
  if (!isNode(node)) return tree
  return replaceAt(tree, path, node.parts.find(part => part !== null) ?? null)
}

/**
 * `tree` with the node at `path` under the operator `op` instead, its parts kept in order: answers
 * `{ tree }`, or `{ problem: 'drop' }` when `op` takes fewer parts and one it would drop is filled.
 * Parts it takes beyond the old ones are empty slots (⿰亻木 as ⿲ is ⿲亻木？).
 */
export function swapOp(tree, path, op) {
  const node = partAt(tree, path), n = arity(op)
  if (!isNode(node) || !n) return { tree }
  if (node.parts.slice(n).some(part => part !== null)) return { problem: 'drop' }
  const parts = Array.from({ length: n }, (_, i) => node.parts[i] ?? null)
  return { tree: replaceAt(tree, path, { op, parts }) }
}

// The composer's drawing of a description is stored under `ids/<sha256 of its NFC form>.svg`, the
// name its manifest gives each sequence it draws, and served under /atlas/. Each text is hashed and
// loaded once a page, keyed by its NFC form: `drawings` holds its URL once the image has loaded, null
// where none is made. A miss is asked again after five minutes, as long as the server answers it as
// missing, so a sequence drawn later or a failed request does not stay as text.
const drawings = new Map(), loading = new Map()
const MISS_MS = 5 * 60 * 1000

async function drawingUrl(text) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))
  return `/atlas/ids/${[...bytes].map(b => b.toString(16).padStart(2, '0')).join('')}.svg`
}

/** The loaded drawing of the description `text`: its URL, null where none is made, undefined until known. */
export const drawn = text => drawings.get(text.normalize('NFC'))

/** Loads the drawing of the description `text` once, answering its URL or null. */
export function loadDrawing(text) {
  text = text.normalize('NFC')
  if (drawings.has(text)) return Promise.resolve(drawings.get(text))
  if (!loading.has(text)) loading.set(text, drawingUrl(text).then(url => new Promise(resolve => {
    const image = new Image()
    image.onload = () => resolve(url)
    image.onerror = () => resolve(null)
    image.src = url
  }), () => null).then(url => {
    drawings.set(text, url)
    loading.delete(text)
    if (url === null) setTimeout(() => drawings.delete(text), MISS_MS)
    return url
  }))
  return loading.get(text)
}
