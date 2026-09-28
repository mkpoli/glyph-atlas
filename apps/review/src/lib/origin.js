import { t } from './i18n.svelte.js'

/**
 * A modern kana's 字源, as its card and the Forms palette show it: the kanji, marked "?" when the
 * article 片仮名 contests it, and a title naming the article's words and any other kanji cited.
 * A 字源 is where the shape came from (の from 乃, ノ from part of 乃), which is not a 字母.
 */
export const originText = origin => (origin ?? []).map(o => o.char + (o.uncertain ? '?' : '')).join(' ')
export const originTitle = origin => (origin ?? []).map(o => [t('origin.source', { text: o.source_text }),
  ...(o.uncertain ? [t('origin.uncertain', { kanji: o.also_cited.join(' ') })] : [])].join(' · ')).join('\n')
