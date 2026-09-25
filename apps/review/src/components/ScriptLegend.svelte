<script>
  import ScriptText from './ScriptText.svelte'
  import { t } from '../lib/i18n.svelte.js'
  // A 구결자 has no glyph in the reader's fonts, so its sample is 口, the form of 古 that 구결 writes
  // for 고, coloured by the script stated beside it.
  const types = [ ['あ', 'hiragana'], ['ア', 'katakana'], ['字', 'kanji'], ['한', 'hangul'], ['口', 'gugyeol'], ['※', 'symbol'] ]
</script>

<div class="script-legend" role="note" aria-label={t('script.legend.label')}>
  {#each types as [character, key]}<span><span aria-hidden="true"><ScriptText text={character} script={key} /></span><span>{t(`script.${key}`)}</span></span>{/each}
</div>

<style>
  .script-legend{display:flex;flex-wrap:wrap;gap:6px 15px;font-size:11px;line-height:1.6;color:var(--muted)}
  .script-legend>span{display:inline-flex;align-items:baseline;gap:5px;white-space:nowrap}
  .script-legend :global(.script-text){font-size:15px;font-weight:500}
</style>
