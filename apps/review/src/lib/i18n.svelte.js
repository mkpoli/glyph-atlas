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
 * The interface language for the browser's preferences: an exact tag first, then the language whose
 * `matches` holds the longest prefix of one the browser asks for, then English.
 */
function detect() {
  const wanted = (typeof navigator === 'undefined' ? [] : navigator.languages ?? [navigator.language]).map(tag => tag.toLowerCase())
  for (const tag of wanted) {
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

function saved() {
  try { return localStorage.getItem('atlas.locale') } catch { return null }
}

let current = $state(byTag[saved()] ? saved() : detect())

export const locale = () => current
export const base = () => byTag[current].base

export function setLocale(tag) {
  if (!byTag[tag]) return
  current = tag
  try { localStorage.setItem('atlas.locale', tag) } catch { /* the choice lasts this visit */ }
}

$effect.root(() => {
  $effect(() => { document.documentElement.lang = current })
})

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
