/**
 * The interface languages: every catalogue in `src/locales`, named after its BCP 47 tag. Its
 * `@locale` entry gives the language's own name, the `base` language the number and plural rules
 * come from, and the browser languages it `matches` on a first visit. `"numerals": "hanzi"` writes
 * its numbers, dates and times in Chinese numerals (一千零五, 二〇二六年九月二十六日); `"classical"` in the
 * classical way, a gap as 有, zero as 無 and the time by double hours (一千有五,
 * 二千有二十六年九月二十六日 未初二刻).
 */
export const LOCALES = Object.entries(import.meta.glob('../locales/*.json', { eager: true, import: 'default' }))
  .map(([path, { '@locale': about, ...messages }]) => ({ tag: path.slice(11, -5), ...about, messages }))
  .sort((a, b) => a.tag.localeCompare(b.tag))
const byTag = Object.fromEntries(LOCALES.map(locale => [locale.tag, locale]))
const en = byTag.en.messages

/**
 * The interface language for a list of preferred language tags, most wanted first: an exact tag first,
 * then the language whose `matches` holds the longest prefix of a wanted tag, then English.
 */
export function negotiate(tags) {
  for (const tag of tags.map(tag => tag.toLowerCase())) {
    const exact = LOCALES.find(locale => locale.tag.toLowerCase() === tag)
    if (exact) return exact.tag
    let best = null, length = 0
    for (const locale of LOCALES) {
      for (const prefix of locale.matches ?? []) {
        if ((tag === prefix || tag.startsWith(`${prefix}-`)) && prefix.length > length) { best = locale.tag; length = prefix.length }
      }
    }
    if (best) return best
  }
  return 'en'
}

/** The cookie that carries a reader's chosen language to the server. */
export const LOCALE_COOKIE = 'atlas.locale'

// The server renders one request at a time and sets this at the start of each render, from the language
// the request negotiated; the browser starts from the same value, so hydration sees the same words.
let current = $state('en')

export const locale = () => current
export const base = () => byTag[current].base
export const isLocale = tag => Boolean(byTag[tag])

/** `path` in language `tag`: English at the path itself, every other language under its own prefix. */
export const localize = (path, tag = current) => tag === 'en' ? path : `/${tag}${path === '/' ? '' : path}`

/**
 * The languages search engines can name in `hreflang`: a two-letter language with an optional script.
 * Literary Chinese (`lzh`) and classical Japanese (`ja-x-classical`) have their pages but no such code.
 */
export const HREFLANG = LOCALES.filter(locale => /^[a-z]{2}(-[A-Z][a-z]{3})?$/.test(locale.tag))

/** The language an address is in, and the address without its prefix. */
export function delocalize(pathname) {
  const [, first, ...rest] = pathname.split('/')
  if (first && first !== 'en' && byTag[first]) return { tag: first, path: '/' + rest.join('/') }
  return { tag: 'en', path: pathname }
}

/** Render in `tag`: the language of the address, set before anything is rendered. */
export function useLocale(tag) {
  if (byTag[tag]) current = tag
}

/** Remember `tag` as the reader's language; the caller moves to the address in that language. */
export function setLocale(tag) {
  if (!byTag[tag]) return
  current = tag
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(tag)}; path=/; max-age=31536000; samesite=lax`
}

const plurals = {}
const pluralOf = count => (plurals[base()] ??= new Intl.PluralRules(base())).select(count)

/**
 * The message for `key` in the current language, English where a translation is missing.
 *
 * `{name}` is replaced by `params.name`. A key with `.one` and `.other` forms is a plural, chosen by
 * `params.count`; the count itself is written in the language's own number format. A language may
 * give a key a `.zero` form of its own, used when `params.count` is 0.
 */
export function t(key, params = {}) {
  const messages = byTag[current].messages
  let text = (params.count === 0 ? messages[`${key}.zero`] : undefined) ?? messages[key] ?? en[key]
  if (text === undefined && 'count' in params) {
    const form = `${key}.${pluralOf(Number(params.count))}`
    text = messages[form] ?? messages[`${key}.other`] ?? en[form] ?? en[`${key}.other`]
  }
  if (text === undefined) return key
  return text.replace(/\{(\w+)\}/g, (whole, name) => name in params
    ? (name === 'count' || typeof params[name] === 'number' ? formatNumber(params[name]) : String(params[name]))
    : whole)
}

const numerals = () => byTag[current].numerals
const hanzi = () => numerals() === 'hanzi' || numerals() === 'classical'
const DIGITS = '〇一二三四五六七八九'

/** 1–9999 with its 千百十 places, a gap written as `gapWord`: 105 is 一百零五, or 一百有五. */
function hanziGroup(value, gapWord) {
  let text = '', gap = false
  for (const [place, unit] of [[1000, '千'], [100, '百'], [10, '十'], [1, '']]) {
    const digit = Math.floor(value / place) % 10
    if (!digit) { gap = text !== ''; continue }
    if (gap) { text += gapWord; gap = false }
    text += DIGITS[digit] + unit
  }
  return text
}

/**
 * A whole number below 10¹⁶ in Chinese numerals, grouped by 萬, 億 and 兆: 12345 is 一萬二千三百四十五,
 * 15 is 十五. The modern form writes 零 for zero and for a gap (10005 is 一萬零五); the classical form
 * joins a gap with 有 and writes zero as 無 (10005 is 一萬有五).
 */
function hanziNumber(value) {
  const classical = numerals() === 'classical'
  const gapWord = classical ? '有' : '零'
  let n = Math.round(Math.abs(value))
  if (!n) return classical ? '無' : '零'
  const groups = []
  for (; n; n = Math.floor(n / 10000)) groups.push(n % 10000)
  let text = '', gap = false
  for (let g = groups.length - 1; g >= 0; g--) {
    if (!groups[g]) { gap = text !== ''; continue }
    if (text && (gap || groups[g] < 1000)) text += gapWord
    text += hanziGroup(groups[g], gapWord) + ['', '萬', '億', '兆'][g]
    gap = false
  }
  return (value < 0 ? '負' : '') + text.replace(/^一十/, '十')
}

/** A number in the current language's format; ∞, NaN and numbers past 兆 keep the base language's digits. */
export function formatNumber(value) {
  const n = Number(value ?? 0)
  return hanzi() && Number.isFinite(n) && Math.abs(n) < 1e16 ? hanziNumber(n) : n.toLocaleString(base())
}

/** A running number on a tile: 01, 02 … in digits, 一, 二 … in Chinese numerals. */
export const formatSerial = value => hanzi() ? formatNumber(value) : String(value).padStart(2, '0')

/**
 * A time of day by the twelve double hours, each split into 初 and 正, then 刻 of fifteen minutes and
 * 分: 00:05 is 子正五分, 13:37 is 未初二刻七分.
 */
function doubleHour(at) {
  const hour = at.getHours(), minute = at.getMinutes()
  const quarter = Math.floor(minute / 15), rest = minute % 15
  return '子丑寅卯辰巳午未申酉戌亥'[Math.floor((hour + 1) / 2) % 12] + (hour % 2 ? '初' : '正')
    + (quarter ? `${hanziNumber(quarter)}刻` : '') + (rest ? `${hanziNumber(rest)}分` : '')
}

const dateFormats = {}

/** A moment in the current language: date and time, or with `{ date: false }` the time alone. Throws on an invalid date. */
export function formatDateTime(value, { date = true } = {}) {
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) throw new RangeError(`Invalid time value: ${value}`)
  if (hanzi()) {
    const time = numerals() === 'classical' ? doubleHour(at) : `${hanziNumber(at.getHours())}時${at.getMinutes() ? `${hanziNumber(at.getMinutes())}分` : ''}`
    if (!date) return time
    const year = numerals() === 'classical' ? hanziNumber(at.getFullYear()) : [...String(at.getFullYear())].map(digit => DIGITS[digit]).join('')
    return `${year}年${hanziNumber(at.getMonth() + 1)}月${hanziNumber(at.getDate())}日 ${time}`
  }
  const key = `${base()} ${date}`
  dateFormats[key] ??= new Intl.DateTimeFormat(base(), date ? { dateStyle: 'medium', timeStyle: 'short' } : { hour: '2-digit', minute: '2-digit' })
  return dateFormats[key].format(at)
}

/**
 * A message split around one of its placeholders, for text that sets that part apart (a bold reading,
 * a glyph): the words before it and after it, in the order the language puts them.
 */
export function around(key, name, params = {}) {
  const text = t(key, params)
  const at = text.indexOf(`{${name}}`)
  return at < 0 ? [text, ''] : [text.slice(0, at), text.slice(at + name.length + 2)]
}

/** The project's name in the interface language, or null where that language uses the English wordmark. */
export function localName() {
  const name = byTag[current].messages['app.name']
  return current !== 'en' && name && name !== en['app.name'] ? name : null
}
