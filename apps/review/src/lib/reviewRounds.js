// Full rounds come first; smaller graphemes follow before any grapheme repeats.
export const MIN_ROUND_SIZE = 6
// Crops a round deals at first.
export const ROUND_BATCH = 48
// Crops each further load adds as the reader scrolls: small enough to arrive quickly and often.
export const MORE_BATCH = 24
// Reference crops of one grapheme shown alongside a round: this many already-confirmed crops,
// then up to this many more already-seen crops.
export const REFERENCE_LIMIT = 12
/** Tile widths (CSS px) offered by Quick Review's tile-size slider, smallest to largest. */
export const QUIZ_TILE_SIZES = [48, 60, 76, 96, 124, 160, 200]
/** Default tile width in standard card mode (6 columns on desktop). */
export const QUIZ_CARDS_SIZE = 160
/** Default tile width in compact thumbnail mode. */
export const QUIZ_COMPACT_SIZE = 76
/** Narrowest tile width that still fits full card metadata and action buttons. */
export const QUIZ_CARDS_MIN = 124

/** Coerce a persisted tile size to a valid stop, falling back when missing or out of set. */
export function normalizeQuizTileSize(value, fallback = QUIZ_CARDS_SIZE) {
  const n = Number(value)
  return Number.isFinite(n) && QUIZ_TILE_SIZES.includes(n) ? n : fallback
}

/** Resolve view mode and tile width when the reader switches between Cards and Compact mode. */
export function switchQuizView(nextView, { cardsSize = QUIZ_CARDS_SIZE, compactSize = QUIZ_COMPACT_SIZE } = {}) {
  const view = nextView === 'compact' ? 'compact' : 'cards'
  if (view === 'compact') return { view, size: normalizeQuizTileSize(compactSize, QUIZ_COMPACT_SIZE) }
  const size = normalizeQuizTileSize(cardsSize, QUIZ_CARDS_SIZE)
  return { view, size: size >= QUIZ_CARDS_MIN ? size : QUIZ_CARDS_SIZE }
}

/** Resolve view mode and tile width when the reader drags the tile-size slider. */
export function resizeQuizTiles(size, currentView = 'cards') {
  const fallback = currentView === 'compact' ? QUIZ_COMPACT_SIZE : QUIZ_CARDS_SIZE
  const nextSize = normalizeQuizTileSize(size, fallback)
  const view = nextSize < QUIZ_CARDS_MIN ? 'compact' : currentView === 'compact' ? 'compact' : 'cards'
  return { view, size: nextSize }
}

/**
 * The form key a crop is sectioned under in Quick Review: its standing claimed form when it has
 * one, else its written character or label. Frozen as `dealtForm` when dealt so marking a crop
 * during a round keeps the tile in place instead of jumping sections.
 */
export function cropSectionForm(item, fallback = '') {
  if (item?.dealtForm) return item.dealtForm
  const claimed = item?.form?.status !== 'disputed' && (item?.form?.values ?? []).find(v => v.text)?.text
  return claimed || item?.written_character || item?.label || fallback
}

/**
 * Partition a round's crops into sections by written form (字樣), ordered by `members` first and
 * then by crop count descending (ties by code point). Within each section, crops keep their
 * incoming order (shape order or dealt order).
 */
export function groupRoundByForm(items, members = [], fallback = '') {
  const buckets = new Map()
  for (const item of items) {
    const form = cropSectionForm(item, fallback)
    let bucket = buckets.get(form)
    if (!bucket) {
      bucket = { key: form || 'other', form, jibo: null, items: [] }
      buckets.set(form, bucket)
    }
    if (!bucket.jibo && item?.jibo) bucket.jibo = item.jibo
    bucket.items.push(item)
  }
  const rank = new Map(members.map((m, i) => [m, i]))
  return [...buckets.values()].sort((a, b) => {
    const ra = rank.has(a.form) ? rank.get(a.form) : Infinity
    const rb = rank.has(b.form) ? rank.get(b.form) : Infinity
    if (ra !== rb) return ra - rb
    if (b.items.length !== a.items.length) return b.items.length - a.items.length
    return a.form < b.form ? -1 : a.form > b.form ? 1 : 0
  })
}
export const automaticCategories = categories => categories.filter(c => c.pending >= MIN_ROUND_SIZE)

/** A round's address, as the site writes a grapheme's (`U+85CF`, `U+304B-U+309A`), with its material and whether
 * it deals suspects alone. */
export function roundAddress(key, production, suspects = false) {
  const query = [production && 'production=' + production, suspects && 'suspects=only'].filter(Boolean).join('&')
  return '/review' + (key ? '/' + key.split(' ').join('-') : '') + (query ? '?' + query : '')
}

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

/** Whether a grapheme's counts hold the round a key names: its own, or that of one of its characters alone. */
export const dealsFor = (group, key) => group.label === key || group.members.some(m => m.label === graphemeText(key))

/**
 * The round a target opens with, or null for none of it: the key `named`, the target's as it is now. An asked one (an
 * address's or a history entry's) is tried whatever the counts hold, since they are the last publication's and the round
 * deals by the tables as they stand; one that deals no crop gives way to the next.
 */
export const openingRound = (available, { target, named, asked }) =>
  target && (asked || available.some(group => dealsFor(group, named))) ? named : null

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

/**
 * A round's first crops, less those in `away` (crops of saves kept on this device): pages from
 * `page(cursor)` until one leaves a crop, so kept crops do not empty a grapheme that holds more. Each
 * further page reaches past at least one kept crop, so at most `away.size` more are read. Answers the
 * last page with the crops left as `items`; its `next` continues the round.
 */
export async function firstCrops(page, away) {
  let cursor = null, result, items = []
  for (let reads = 0; reads <= away.size; reads++) {
    result = await page(cursor)
    items = result.items.filter(item => !away.has(item.id))
    if (items.length || !result.next) return { ...result, items }
    cursor = result.next
  }
  return { ...result, items }
}
