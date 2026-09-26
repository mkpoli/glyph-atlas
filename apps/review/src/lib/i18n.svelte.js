/**
 * The interface languages: every catalogue in `src/locales`, named after its BCP 47 tag. Its
 * `@locale` entry gives the language's own name, the `base` language the number and plural rules
 * come from, and the browser languages it `matches` on a first visit.
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

/** A number in the current language's format. */
export const formatNumber = value => Number(value ?? 0).toLocaleString(base())

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
