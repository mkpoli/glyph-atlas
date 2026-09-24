/** A transcription bucket cannot identify the character written in its image. */
export const isUnassigned = item => item?.identity_status === 'unassigned'
  || (item?.written_character === null && item?.identity_basis === 'normalized_transcription')

export const writtenLabel = item => isUnassigned(item) ? 'Unassigned'
  : item?.written_character ?? item?.label ?? item?.char ?? ''

export function visualGroup(item) {
  if (item?.visual_group?.id) return { id: item.visual_group.id,
    label: item.visual_group.label ?? 'Similar forms' }
  if (isUnassigned(item)) return { id: 'unassigned', label: 'Unassigned' }
  const char = writtenLabel(item)
  return { id: 'character:' + char, label: char }
}

export function matchesVisualGroup(item, group) {
  if (!group) return true
  return group === 'unassigned' ? isUnassigned(item) : item?.visual_group?.id === group
}


const SCRIPT_NAMES = { hiragana: 'Hiragana', hentaigana: 'Hiragana', katakana: 'Katakana',
  han: 'Kanji', kanji: 'Kanji', symbol: 'Symbol', mixed: 'Mixed', unknown: 'Unknown' }

// ー belongs to Unicode's Common script and to `symbol` in the character table; it is coloured as katakana.
const KATAKANA_MARKS = new Set(['ー'])

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
    if (/\p{Script=Hiragana}/u.test(char) || (cp >= 0x1B001 && cp <= 0x1B11F) || cp === 0x1B123) return 'hiragana'
    if (/\p{Script=Katakana}/u.test(char) || cp === 0x2A708 || cp === 0x1B000 || (cp >= 0x1B120 && cp <= 0x1B122)
      || (cp >= 0x1B124 && cp <= 0x1B128) || cp === 0x1B168 || (cp >= 0x1AFF0 && cp <= 0x1AFFF)) return 'katakana'
    if (/\p{Script=Han}/u.test(char) || (cp >= 0x20000 && cp <= 0x2FFFF) || (cp >= 0x30000 && cp <= 0x3347F)) return 'kanji'
    if (/[\p{Symbol}\p{Punctuation}]/u.test(char)) return 'symbol'
    return 'unknown'
  }))
  const key = kinds.size > 1 ? 'mixed' : [...kinds][0] ?? 'unknown'
  return { key, label: SCRIPT_NAMES[key] }
}

const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' })

export function scriptParts(text, stated = '') {
  const parts = [...graphemes.segment(text ?? '')].map(part => part.segment)
  return parts.map(part => ({ text: part, ...scriptInfo(part, parts.length === 1 ? stated : '') }))
}
