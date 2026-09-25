import en from '../locales/en.json'
import ja from '../locales/ja.json'
import jaClassical from '../locales/ja-x-classical.json'
import zhHans from '../locales/zh-Hans.json'
import zhHant from '../locales/zh-Hant.json'
import ko from '../locales/ko.json'
import koKore from '../locales/ko-Kore.json'

/**
 * The interface languages, each named in itself. `ja-x-classical` is Japanese in the old character
 * forms and historical kana with literary grammar; `ko-Kore` is Korean in mixed Hangul and Hanja.
 * `base` is the language the number and plural rules come from.
 */
export const LOCALES = [
  { tag: 'en', name: 'English', base: 'en', messages: en },
  { tag: 'ja', name: '日本語', base: 'ja', messages: ja },
  { tag: 'ja-x-classical', name: '日本語（文語）', base: 'ja', messages: jaClassical },
  { tag: 'zh-Hans', name: '简体中文', base: 'zh-Hans', messages: zhHans },
  { tag: 'zh-Hant', name: '繁體中文', base: 'zh-Hant', messages: zhHant },
  { tag: 'ko', name: '한국어', base: 'ko', messages: ko },
  { tag: 'ko-Kore', name: '韓國語（國漢文）', base: 'ko', messages: koKore },
]
const byTag = Object.fromEntries(LOCALES.map(locale => [locale.tag, locale]))

/** The first interface language the browser asks for, or English. */
function detect() {
  for (const wanted of typeof navigator === 'undefined' ? [] : navigator.languages ?? [navigator.language]) {
    const tag = wanted.toLowerCase()
    if (tag.startsWith('ja')) return 'ja'
    if (tag.startsWith('ko')) return 'ko'
    if (/^zh-(hant|tw|hk|mo)/.test(tag)) return 'zh-Hant'
    if (tag.startsWith('zh')) return 'zh-Hans'
    if (tag.startsWith('en')) return 'en'
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
