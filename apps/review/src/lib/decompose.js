// What the IDS editor's "Take parts from" box reads: the characters or the description typed there,
// each character's descriptions from `/layers/structure` (BabelStone IDS) with the source regions of
// each, and the way the tree of them is stepped through by keys.
import { isComponent, isDescription, leaves, parse, unalias } from './ids.js'

/** How many characters the box decomposes at once. */
export const ROOTS = 8
/** How many characters deep a part can be opened, counting the one typed. */
export const DEPTH = 8
/** The most parts the component search takes (the Worker's COMPONENT_PARTS). */
export const SEARCH_PARTS = 8

const SELECTOR = /^[︀-️\u{E0100}-\u{E01EF}]$/u
/** A description's tokens: BabelStone's unencoded component `{n}` is one, a variation selector goes
 *  with the character before it. */
export function tokens(text) {
  const out = []
  for (const [token] of (text ?? '').matchAll(/\{\d+\}|./gsu)) {
    if (SELECTOR.test(token) && out.length) out[out.length - 1] += token
    else out.push(token)
  }
  return out
}
/** The number of BabelStone's unencoded component `{n}`, or null for any other token. */
export const unencoded = token => /^\{(\d+)\}$/.exec(token ?? '')?.[1] ?? null

/** The character `/layers/structure` is asked about for a part: its variation selector dropped. */
export const lookupChar = part => [...(part ?? '')][0] ?? ''

/** Whether the structure route can be asked about `part`: one encoded component, not ？. */
export const decomposable = part => typeof part === 'string' && isComponent(lookupChar(part)) && lookupChar(part) !== '？'

/**
 * What the box decomposes for the text typed: a description the editor reads (typed with operators or
 * their ASCII names) as one sequence, else each component character once, in order, at most ROOTS.
 * Answers `[{ kind: 'sequence', text, tree }]` or `[{ kind: 'char', char }]`.
 */
export function roots(text) {
  const written = unalias((text ?? '').trim())
  if (!written) return []
  if (isDescription(written)) return [{ kind: 'sequence', text: written, tree: parse(written).tree }]
  const seen = new Set()
  for (const token of tokens(written)) if (decomposable(token) && !seen.has(token)) seen.add(token)
  return [...seen].slice(0, ROOTS).map(char => ({ kind: 'char', char }))
}

/**
 * A character's descriptions as the box lists them: those with regional shapes first, with their
 * regions (BabelStone's source letters, `HJKP`), then any other. Each is `{ text, regions, tree }`,
 * the tree null where the editor cannot read the sequence (an unencoded `{n}`, a part no description
 * may name). The structure route drops such sequences today, so this only guards against one that
 * reaches the box anyway: it is shown as text and not taken.
 */
export function variants(found) {
  if (!found) return []
  const listed = new Map()
  for (const { text, regions } of found.shapes ?? []) if (text && !listed.has(text)) listed.set(text, regions ?? '')
  for (const text of found.sequences ?? []) if (text && !listed.has(text)) listed.set(text, '')
  return [...listed].map(([text, regions]) => {
    const read = parse(text)
    return { text, regions, tree: read.problem ? null : read.tree }
  })
}

/** The encoded components an unreadable sequence names, once each, so they can still be opened. */
export const namedParts = text => [...new Set(tokens(text).filter(decomposable))]

const PART = /^[\p{Script=Han}㇀-㇯]$/u
/**
 * The parts the component search is asked for: two or more typed characters as they are, else the
 * components of the one typed (of its first description the editor reads), counted, so 林 asks for
 * 木 twice. Only ideographs, radicals and strokes are asked for, at most SEARCH_PARTS; fewer than two
 * ask for nothing (`''`).
 */
export function searchParts(found, known) {
  let parts
  if (found.length === 1 && found[0].kind === 'sequence') parts = leaves(found[0].tree).map(leaf => leaf.char)
  else if (found.length === 1) {
    const tree = variants(known[lookupChar(found[0].char)]).find(v => v.tree)?.tree
    parts = tree ? leaves(tree).map(leaf => leaf.char) : []
  } else parts = found.map(root => root.char)
  const asked = parts.filter(part => typeof part === 'string' && PART.test(lookupChar(part).normalize('NFKC'))).map(lookupChar)
  return asked.length >= 2 ? asked.slice(0, SEARCH_PARTS).join('') : ''
}

/**
 * Where a key moves in a tree shown as a list of rows, each `{ level, open, openable, swaps }` in
 * reading order, from the row `at` (and, with `swap`, from that one of its swaps): ArrowDown and
 * ArrowUp to the next row and the one before, Home and End to the first and last. ArrowRight opens a
 * closed row, else steps through its swaps, then into it when it is open; ArrowLeft steps back through
 * the swaps to the row, then closes an open row or steps out to its parent. Answers `{ focus }` (a row
 * index), `{ toggle }` (the row to open or close), `{ swap }` (a swap's index, null for the row
 * itself), or null for a key the tree leaves alone.
 */
export function treeKey(rows, at, key, swap = null) {
  const row = rows[at]
  if (!row) return null
  const swaps = row.swaps?.length ?? 0
  switch (key) {
    case 'ArrowDown': return { focus: Math.min(at + 1, rows.length - 1) }
    case 'ArrowUp': return { focus: Math.max(at - 1, 0) }
    case 'Home': return { focus: 0 }
    case 'End': return { focus: rows.length - 1 }
    case 'ArrowRight':
      if (row.openable && !row.open) return { toggle: at }
      if ((swap ?? -1) < swaps - 1) return { swap: (swap ?? -1) + 1 }
      if (row.open && rows[at + 1]?.level > row.level) return { focus: at + 1 }
      return { focus: at }
    case 'ArrowLeft': {
      if (swap !== null) return { swap: swap > 0 ? swap - 1 : null }
      if (row.openable && row.open) return { toggle: at }
      for (let k = at - 1; k >= 0; k--) if (rows[k].level < row.level) return { focus: k }
      return { focus: at }
    }
    default: return null
  }
}
