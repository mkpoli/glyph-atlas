import { t, formatYear } from './i18n.svelte.js'

// When the book a crop comes from was copied, printed or composed, as the site shows it. A listing item
// carries `dating` (`witness`: the copy itself, `composed`: its text), each a year range with its
// precision and qualifier, the words its source wrote and, for a Japanese date, the HuTime request that
// converted it. The inspector's record also lists every claim (`dates`) with its source.

/** HuTime's converter, where a reader can convert any other Japanese date. */
export const HUTIME_FORM = 'https://www.hutime.jp/basicdata/calendar/form.html'

/** The names of the sources a date comes from, as they name themselves. */
const SOURCES = {
  kokusho: '国書データベース', 'ainu-records': 'アイヌ関連資料', 'honkoku-data': 'みんなで翻刻',
  'hng-basic-data': 'HNG', 'hng-kiridashi-data': 'HNG', 'hdic-krm': 'HDIC', 'hdic-ktb': 'HDIC', 'hdic-tsj': 'HDIC',
  khs: '국가유산청', 'hangeul-museum': '국립한글박물관', nlk: '국립중앙도서관', wikisource: 'Wikisource',
  'codh-kokatsuji': '国立国会図書館',
}
export const sourceName = id => id === 'iiif-manifests' ? t('date.source.holder') : SOURCES[id] ?? id

const ordinal = n => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th'}`

/**
 * A date in a few characters, honest about its range and doubt: 1791, c. 1800, 1789–1801, 18th c.,
 * after 1855. A named period with no years (江戸後期) is written as its source wrote it.
 */
export function dateLabel(date) {
  if (!date) return ''
  const { start, end, precision, qualifier, uncertain } = date
  if (start == null && end == null) return date.text
  let text
  if (qualifier === 'after') text = t('date.after', { date: formatYear(start) })
  else if (qualifier === 'before') text = t('date.before', { date: formatYear(end) })
  else if (precision === 'century' && start % 100 === 1 && end % 100 === 0) {
    const first = (start - 1) / 100 + 1, last = end / 100
    text = first === last ? t('date.century', { n: first, ordinal: ordinal(first) })
      : t('date.centuries', { first, last, firstOrdinal: ordinal(first), lastOrdinal: ordinal(last) })
  } else if (precision === 'decade') text = t('date.decade', { year: formatYear(start) })
  else if (start === end) text = formatYear(start)
  else text = t('date.range', { start: formatYear(start), end: formatYear(end) })
  if (qualifier === 'circa') text = t('date.circa', { date: text })
  return uncertain ? t('date.uncertain', { date: text }) : text
}

/** What one dated event says: copied 1271, printed 1791, text composed c. 1000. */
export const eventLabel = (kind, date) => t(`date.kind.${kind}`, { date: dateLabel(date) })

/**
 * The dates a crop's book shows, in one line: this copy's, then its text's; "no date recorded" when no
 * source dates the copy. A listing that gives no `dating` at all says nothing.
 */
export function datingLine(item) {
  if (!item?.dating) return null
  const { witness, composed } = item.dating
  return [witness ? eventLabel(witness.kind, witness) : t('date.undated'), composed ? eventLabel('composed', composed) : null]
    .filter(Boolean).join(' · ')
}

/** The same in a tile's corner: the copy's years, or nothing for a copy with none (a named period has no room there). */
export const tileDate = item => {
  const date = item?.dating?.witness
  return date && (date.start != null || date.end != null) ? dateLabel(date) : ''
}

/**
 * What the date says on hover: each dated event as its source wrote it, with the source, whether the
 * sources disagree, and how a Japanese date was converted.
 */
export function datingTitle(item) {
  const claims = item?.dates ?? []
  const axes = Object.values(item?.dating ?? {})
  const lines = claims.length
    ? claims.map(c => `${eventLabel(c.kind, c)}: ${c.text} (${sourceName(c.source)})`)
    : axes.map(a => `${eventLabel(a.kind, a)}: ${a.text} (${sourceName(a.source)})`)
  if (axes.some(a => a.status === 'disputed')) lines.push(t('date.disputed'))
  if ([...axes, ...claims].some(a => a.calendar === 'japanese')) lines.push(t('date.hutime', { form: HUTIME_FORM }))
  return lines.join('\n')
}
