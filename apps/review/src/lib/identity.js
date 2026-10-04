/** A transcription bucket cannot identify the character written in its image. */
export const isUnassigned = item => item?.identity_status === 'unassigned'
  || (item?.written_character === null && item?.identity_basis === 'normalized_transcription')

/** The grapheme a crop belongs to, as a character ('U+3042' → 'あ'), or null when it is not known.
 * A crop can know its grapheme while its written form is still unassigned. */
export function graphemeChar(item) {
  const point = item?.grapheme?.code_point ?? item?.grapheme
  const match = typeof point === 'string' && /^U\+([0-9A-F]{4,6})$/i.exec(point)
  return match ? String.fromCodePoint(parseInt(match[1], 16)) : null
}
export const writtenLabel = item => isUnassigned(item) ? 'Unassigned'
  : item?.written_character ?? item?.label ?? item?.char ?? ''

export function visualGroup(item) {
  if (item?.visual_group?.id) return { id: item.visual_group.id,
    label: item.visual_group.label ?? 'Similar forms' }
  if (isUnassigned(item)) return { id: 'unassigned', label: 'Unassigned' }
  // A crop no shape analysis placed is in no group; its tile already shows the character it is written as.
  return { id: 'ungrouped', label: null }
}

export function matchesVisualGroup(item, group) {
  if (!group) return true
  return group === 'unassigned' ? isUnassigned(item) : item?.visual_group?.id === group
}


const SCRIPT_NAMES = { hiragana: 'Hiragana', hentaigana: 'Hiragana', katakana: 'Katakana',
  han: 'Kanji', kanji: 'Kanji', hangul: 'Hangul', gugyeol: 'Gugyeol', latin: 'Latin', symbol: 'Symbol', mixed: 'Mixed', unknown: 'Unknown' }

// ー belongs to Unicode's Common script and to `symbol` in the character table; it is coloured as katakana.
const KATAKANA_MARKS = new Set(['ー'])
// The operators of an ideographic description sequence (⿰亻哥 is 亻 beside 哥), with the number of
// descriptions each takes. A sequence describes one Han character Unicode lacks.
const IDS_ARITY = new Map([...[...'⿰⿱⿴⿵⿶⿷⿸⿹⿺⿻⿼⿽㇯'].map(c => [c, 2]), ...[...'⿲⿳'].map(c => [c, 3]), ...[...'⿾⿿〾'].map(c => [c, 1])])

export function scriptInfo(text, stated = '') {
  if (text && [...text].every(char => KATAKANA_MARKS.has(char))) return { key: 'katakana', label: SCRIPT_NAMES.katakana }
  if (stated && stated !== 'unknown' && SCRIPT_NAMES[stated]) {
    const key = stated === 'han' ? 'kanji' : stated === 'hentaigana' ? 'hiragana' : stated
    return { key, label: SCRIPT_NAMES[stated] }
  }
  const kinds = new Set([...text ?? ''].filter(char => !/[\p{Mark}\s]/u.test(char)
    && !(char.codePointAt(0) >= 0xE0100 && char.codePointAt(0) <= 0xE01EF)).map(char => {
    const cp = char.codePointAt(0)
    if (KATAKANA_MARKS.has(char)) return 'katakana'
    if (IDS_ARITY.has(char)) return 'kanji'
    if (/\p{Script=Hiragana}/u.test(char) || (cp >= 0x1B001 && cp <= 0x1B11F) || cp === 0x1B123) return 'hiragana'
    if (/\p{Script=Katakana}/u.test(char) || cp === 0x2A708 || cp === 0x1B000 || (cp >= 0x1B120 && cp <= 0x1B122)
      || (cp >= 0x1B124 && cp <= 0x1B128) || cp === 0x1B168 || (cp >= 0x1AFF0 && cp <= 0x1AFFF)) return 'katakana'
    if (/\p{Script=Han}/u.test(char) || (cp >= 0x20000 && cp <= 0x2FFFF) || (cp >= 0x30000 && cp <= 0x3347F)) return 'kanji'
    if (/\p{Script=Hangul}/u.test(char)) return 'hangul'
    if (cp >= 0xF67E && cp <= 0xF77C) return 'gugyeol'
    if (/\p{Script=Latin}/u.test(char)) return 'latin'
    if (/[\p{Symbol}\p{Punctuation}]/u.test(char)) return 'symbol'
    return 'unknown'
  }))
  const key = kinds.size > 1 ? 'mixed' : [...kinds][0] ?? 'unknown'
  return { key, label: SCRIPT_NAMES[key] }
}

const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' })

// A description sequence is one part: its operator and every description the operators take.
function described(segments) {
  const parts = []
  for (let at = 0; at < segments.length;) {
    let text = '', owed = 1
    do {
      const segment = segments[at++]
      text += segment
      owed += (IDS_ARITY.get(segment) ?? 0) - 1
    } while (owed > 0 && at < segments.length && IDS_ARITY.has(text[0]))
    parts.push(text)
  }
  return parts
}

export function scriptParts(text, stated = '') {
  const parts = described([...graphemes.segment(text ?? '')].map(part => part.segment))
  return parts.map(part => ({ text: part, ...scriptInfo(part, parts.length === 1 ? stated : '') }))
}
