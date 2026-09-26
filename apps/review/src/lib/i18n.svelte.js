/**
 * The interface languages: every catalogue in `src/locales`, named after its BCP 47 tag. Its
 * `@locale` entry gives the language's own name, the `base` language the number and plural rules
 * come from, and the browser languages it `matches` on a first visit. `"numerals": "hanzi"` writes
 * its numbers, dates and times in Chinese numerals (一千二百三十四, 二〇二六年九月二十六日).
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

/** Render in `tag`: the language the request negotiated, set before anything is rendered. */
export function useLocale(tag) {
  if (byTag[tag]) current = tag
}

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
 * `params.count`; the count itself is written in the language's own number format.
 */
export function t(key, params = {}) {
  const messages = byTag[current].messages
  let text = messages[key] ?? en[key]
  if (text === undefined && 'count' in params) {
    const form = `${key}.${pluralOf(Number(params.count))}`
    text = messages[form] ?? messages[`${key}.other`] ?? en[form] ?? en[`${key}.other`]
  }
  if (text === undefined) return key
  return text.replace(/\{(\w+)\}/g, (whole, name) => name in params
    ? (name === 'count' || typeof params[name] === 'number' ? formatNumber(params[name]) : String(params[name]))
    : whole)
}

const hanzi = () => byTag[current].numerals === 'hanzi'
const DIGITS = '零一二三四五六七八九'

/** 1–9999 with its 千百十 places, a gap inside written 零: 105 is 一百零五. */
function hanziGroup(value) {
  let text = '', gap = false
  for (const [place, unit] of [[1000, '千'], [100, '百'], [10, '十'], [1, '']]) {
    const digit = Math.floor(value / place) % 10
    if (!digit) { gap = text !== ''; continue }
    if (gap) { text += '零'; gap = false }
    text += DIGITS[digit] + unit
  }
  return text
}

/** A whole number in Chinese numerals, grouped by 萬, 億 and 兆: 12345 is 一萬二千三百四十五, 15 is 十五. */
export function hanziNumber(value) {
  let n = Math.round(Math.abs(value))
  if (!n) return '零'
  const groups = []
  for (; n; n = Math.floor(n / 10000)) groups.push(n % 10000)
  let text = '', gap = false
  for (let g = groups.length - 1; g >= 0; g--) {
    if (!groups[g]) { gap = text !== ''; continue }
    if (text && (gap || groups[g] < 1000)) text += '零'
    text += hanziGroup(groups[g]) + ['', '萬', '億', '兆'][g]
    gap = false
  }
  return (value < 0 ? '負' : '') + text.replace(/^一十/, '十')
}

/** A number in the current language's format. */
export const formatNumber = value => hanzi() ? hanziNumber(Number(value ?? 0)) : Number(value ?? 0).toLocaleString(base())

/** A running number on a tile: 01, 02 … in digits, 一, 二 … in Chinese numerals. */
export const formatSerial = value => hanzi() ? hanziNumber(value) : String(value).padStart(2, '0')

/** A moment in the current language: date and time, or with `{ date: false }` the time alone. */
export function formatDateTime(value, { date = true } = {}) {
  const at = new Date(value)
  if (hanzi()) {
    const time = `${hanziNumber(at.getHours())}時${at.getMinutes() ? `${hanziNumber(at.getMinutes())}分` : ''}`
    if (!date) return time
    const year = [...String(at.getFullYear())].map(digit => '〇一二三四五六七八九'[digit]).join('')
    return `${year}年${hanziNumber(at.getMonth() + 1)}月${hanziNumber(at.getDate())}日 ${time}`
  }
  return new Intl.DateTimeFormat(base(), date ? { dateStyle: 'medium', timeStyle: 'short' } : { hour: '2-digit', minute: '2-digit' }).format(at)
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
