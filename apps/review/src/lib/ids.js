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
 */
export function parse(text) {
  const chars = [...text ?? '']
  if (!chars.length || chars.length > LONGEST || !isSequence(text) || /[\p{Cc}\p{Cf}\p{Z}]/u.test(text)) return { problem: 'character' }
  let at = 0
  const read = () => {
    const char = chars[at++]
    if (char === undefined) throw 'missing'
    const n = arity(char)
    if (!n) {
      if (!isComponent(char)) throw 'component'
      return isSelector(chars[at]) ? char + chars[at++] : char
    }
    return { op: char, parts: Array.from({ length: n }, read) }
  }
  try {
    const tree = read()
    return at < chars.length ? { problem: 'extra' } : { tree }
  } catch (problem) { return { problem } }
}

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

/** A tree written back as a description. */
export const write = tree => typeof tree === 'string' ? tree : tree.op + tree.parts.map(write).join('')

/** Every component of a tree with its path (the part indices down to it), in reading order. */
export function leaves(tree, path = []) {
  return typeof tree === 'string' ? [{ path, char: tree }] : tree.parts.flatMap((part, i) => leaves(part, [...path, i]))
}

/** `tree` with the part at `path` replaced by `part` (a component's text or a tree). */
export function replaceAt(tree, path, part) {
  if (!path.length) return part
  const [head, ...rest] = path
  return { op: tree.op, parts: tree.parts.map((p, i) => i === head ? replaceAt(p, rest, part) : p) }
}

/** The part at `path`. */
export const partAt = (tree, path) => path.reduce((node, i) => node.parts[i], tree)

/** `node` under `op`: the node first and ？ for every other part the operator takes, so a component
 *  can be split in place (⿰ round 失 gives ⿰失？). */
export const wrap = (op, node) => ({ op, parts: [node, ...Array(arity(op) - 1).fill(UNKNOWN)] })

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
