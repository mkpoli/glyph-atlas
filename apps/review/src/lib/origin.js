import { t } from './i18n.svelte.js'

/**
 * The title of a modern kana's 字源, which its card and the Forms palette show with `OriginText`:
 * the article 片仮名's words and any other kanji cited where it contests the 字源. A 字源 is where
 * the shape came from (の from 乃, ノ from part of 乃), which is not a 字母.
 */
export const originTitle = origin => (origin ?? []).map(o => [t('origin.source', { text: o.source_text }),
  ...(o.uncertain ? [t('origin.uncertain', { kanji: o.also_cited.join(' ') })] : [])].join(' · ')).join('\n')
