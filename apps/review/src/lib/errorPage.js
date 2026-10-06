// What the error page says and draws: which case an error is, the address it may have meant, the
// status code as a grid of cells, and the crops dealt into the code's cells. It holds no interface
// strings, so tests import it bare.
import { runText } from './runs.js'

/** Each digit as five rows of three cells, a lit cell `1`. */
const DIGITS = {
  0: ['111', '101', '101', '101', '111'], 1: ['010', '110', '010', '010', '111'], 2: ['111', '001', '111', '100', '111'],
  3: ['111', '001', '111', '001', '111'], 4: ['101', '101', '111', '001', '001'], 5: ['111', '100', '111', '001', '111'],
  6: ['111', '100', '111', '101', '111'], 7: ['111', '001', '001', '001', '001'], 8: ['111', '101', '111', '101', '111'],
  9: ['111', '101', '111', '001', '111'],
}

/**
 * The status code as one grid: `columns` across five rows, a blank column between digits, and
 * `cells` in reading order, each `{ row, column, lit }`. Lit cells are numbered (`slot`) in the
 * order crops are dealt into them.
 */
export function codeCells(status) {
  const digits = String(status).replace(/\D/g, '').split('').filter(d => DIGITS[d])
  const columns = Math.max(0, digits.length * 4 - 1), cells = []
  let slot = 0
  for (let row = 0; row < 5; row++) digits.forEach((digit, i) => {
    for (let x = 0; x < 3; x++) {
      const lit = DIGITS[digit][row][x] === '1'
      cells.push({ row: row + 1, column: i * 4 + x + 1, lit, slot: lit ? slot++ : -1 })
    }
  })
  return { columns, cells, lit: slot }
}

/** What an error is, as the page tells it. */
export function errorCase({ status, code, message = '', routeId = '', online = true }) {
  if (!online || (status >= 500 && /failed to fetch|networkerror|load failed|network connection/i.test(message))) return 'offline'
  if (status === 503 && code === 'busy') return 'busy'
  if (status >= 500) return 'server'
  if (status === 404) {
    if (/\/(crop|corpus)\/\[id\]$/.test(routeId)) return 'crop'
    if (/\/(character|chronology)\/\[code\]$/.test(routeId)) return 'character'
    if (/\/forms\/\[\[family\]\]$/.test(routeId)) return 'forms'
    return 'notFound'
  }
  return 'other'
}

const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' })
const pointOf = char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join('-')

/**
 * The page a missing address may have meant, from its last part (the path without its language):
 * a character (`字`, `U+5B57`), a sequence of two to eight characters, or a crop id (`codh:…`).
 * `{ kind, path, text }`, or null. A path the router already took for that kind is not offered again.
 */
export function suggestion(path, kind = 'notFound') {
  const parts = path.split('/').filter(Boolean)
  let last = parts.at(-1) ?? ''
  try { last = decodeURIComponent(last) } catch { return null }
  last = last.trim()
  if (!last) return null
  // A code point as a character's address writes it (`gallery.js` slug), in any case.
  if (/^u\+[0-9a-f]{4,6}(-u\+[0-9a-f]{4,6})*$/i.test(last)) {
    const point = last.toUpperCase(), values = point.split('-').map(p => parseInt(p.slice(2), 16))
    if (values.some(value => value > 0x10ffff)) return null
    return kind === 'character' && point === last ? null : { kind: 'character', path: '/character/' + point, text: String.fromCodePoint(...values) }
  }
  if (/^[^\s\x00-\x7f]+$/u.test(last) && [...graphemes.segment(last)].length === 1) return { kind: 'character', path: '/character/' + pointOf(last), text: last }
  if (runText(last)) return { kind: 'sequence', path: '/sequence/' + encodeURIComponent(last), text: last }
  if (kind !== 'crop' && /^[a-z][a-z0-9-]*:[^\s/]+$/i.test(last)) return { kind: 'crop', path: '/crop/' + encodeURIComponent(last), text: last }
  return null
}

/** How many of the most frequent pairs the crops are drawn from, and how many pairs are read. */
const PAIRS_FROM = 12, PAIRS_READ = 2

/**
 * Crops for the code's cells: two of the site's most frequent pairs, picked at random, and the first
 * page of each pair's occurrences. Both reads are ones Explore and a sequence page make, which the
 * Worker keeps at the edge per catalogue version, and a sequence page asks for the same first page,
 * so a reader here shares its copy. One try each, no waiting on a busy database: the page is whole
 * without them. `{ crops: [{ id, origin, image, label, … }], runs: [text] }`.
 */
export async function sampleCrops({ send = fetch, signal, random = Math.random } = {}) {
  const read = async path => {
    const response = await send(path, { signal, headers: { accept: 'application/json' } })
    if (!response.ok) throw new Error(`${path}: ${response.status}`)
    return response.json()
  }
  const pairs = ((await read('/atlas/ngrams/2')).items ?? []).slice(0, PAIRS_FROM).map(item => item.text)
  const runs = shuffle(pairs, random).slice(0, PAIRS_READ)
  const pages = await Promise.allSettled(runs.map(text => read('/atlas/runs?' + new URLSearchParams({ text, offset: '0' }))))
  const seen = new Set(), crops = []
  const kept = runs.filter((_, i) => pages[i].status === 'fulfilled')
  for (const result of pages) if (result.status === 'fulfilled') for (const occurrence of result.value.items ?? [])
    for (const crop of occurrence.crops ?? []) if (crop.image && !seen.has(crop.id)) { seen.add(crop.id); crops.push(crop) }
  return { crops: shuffle(crops, random), runs: kept }
}

/** A copy of `list` in random order. */
export function shuffle(list, random = Math.random) {
  const out = [...list]
  for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [out[i], out[j]] = [out[j], out[i]] }
  return out
}
