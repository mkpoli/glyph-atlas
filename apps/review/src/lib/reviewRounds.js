// Full rounds come first; smaller graphemes follow before any grapheme repeats.
export const MIN_ROUND_SIZE = 6
// Crops a round deals at first.
export const ROUND_BATCH = 48
// Crops each further load adds as the reader scrolls: small enough to arrive quickly and often.
export const MORE_BATCH = 24
// Reference crops of one grapheme shown alongside a round: this many already-confirmed crops,
// then up to this many more already-seen crops.
export const REFERENCE_LIMIT = 12
export const automaticCategories = categories => categories.filter(c => c.pending >= MIN_ROUND_SIZE)

/** A round's address, as the site writes a grapheme's (`U+85CF`, `U+304B-U+309A`), with its material. */
export const roundAddress = (key, production) =>
  '/review' + (key ? '/' + key.split(' ').join('-') : '') + (production ? '?production=' + production : '')

/** A grapheme key (`U+306F`, or a label's own code points) as the text it names. */
export const graphemeText = key => key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('')
const codesOf = label => [...label].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')

/**
 * The rounds a catalogue's character counts make: one per grapheme, holding its characters. 仮 and 假
 * are one round, and は's holds ハ and its hentaigana. Each is keyed by its grapheme (`label`), counts
 * every character's pending crops, and lists its characters with the most pending first.
 */
export function roundGraphemes(categories) {
  const groups = new Map()
  for (const c of categories) {
    const key = c.grapheme ?? codesOf(c.label)
    const group = groups.get(key) ?? { label: key, char: graphemeText(key), pending: 0, members: [] }
    group.pending += c.pending; group.members.push({ label: c.label, pending: c.pending })
    groups.set(key, group)
  }
  for (const group of groups.values()) group.members.sort((a, b) => b.pending - a.pending || a.label.localeCompare(b.label))
  return [...groups.values()].sort((a, b) => b.pending - a.pending || a.label.localeCompare(b.label))
}

/** The next grapheme to review: an unvisited full round, then an unvisited smaller one, largest first. */
export function nextGrapheme(categories, current, history, seed) {
  const others = categories.filter(c => c.pending > 0 && c.label !== current)
  const visited = new Set(history.map(round => round.grapheme))
  const fresh = others.filter(c => !visited.has(c.label))
  const full = automaticCategories(fresh)
  if (full.length) return full[seed % full.length].label
  if (fresh.length) return fresh.reduce((best, c) => c.pending > best.pending ? c : best).label
  const pool = automaticCategories(others).length ? automaticCategories(others) : others
  return pool.length ? pool[seed % pool.length].label : null
}

/**
 * The reference strip for one grapheme: already-confirmed crops, then already-seen crops that
 * were not already counted as confirmed, each tagged with the state it stands for.
 *
 * A crop counted once: a crop that is both checked and seen (a stale `seen` record from before
 * it was confirmed) shows only as confirmed.
 */
export function mergeReferences(checked, seen, limit = REFERENCE_LIMIT) {
  const checkedIds = new Set(checked.map(item => item.id))
  const confirmed = checked.slice(0, limit).map(item => ({ ...item, referenceState: 'checked' }))
  const others = seen.filter(item => !checkedIds.has(item.id)).slice(0, limit)
    .map(item => ({ ...item, referenceState: 'seen' }))
  return [...confirmed, ...others]
}
