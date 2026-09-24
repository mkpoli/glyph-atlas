<script>
  import ScriptText from './ScriptText.svelte'
  const types = [ ['あ', 'Hiragana'], ['ア', 'Katakana'], ['字', 'Kanji'], ['※', 'Symbol'] ]
</script>

<div class="script-legend" role="note" aria-label="Character color legend">
  {#each types as [character, label]}<span><span aria-hidden="true"><ScriptText text={character} /></span><span>{label}</span></span>{/each}
</div>

<style>
  .script-legend{display:flex;flex-wrap:wrap;gap:6px 15px;font-size:11px;line-height:1.6;color:var(--muted)}
  .script-legend>span{display:inline-flex;align-items:baseline;gap:5px;white-space:nowrap}
  .script-legend :global(.script-text){font-size:15px;font-weight:500}
</style>
