<script>
  // A form written as an ideographic description sequence for a selection, as a crop's form picker
  // writes one: a button that opens the composer, which hands the whole sequence to `onuse` and closes.
  // `char` is the character the composer starts from when nothing was typed.
  import IdsComposer from './IdsComposer.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { char = '', disabled = false, title, onuse } = $props()
  let open = $state(false)
</script>

<div class="ids-pick">
  <button type="button" class="ids-open" {disabled} {title} aria-expanded={open} onclick={() => open = !open}>{t('form.ids.compose')}</button>
  {#if open && !disabled}
    <div class="ids-panel"><IdsComposer {disabled} {char} onuse={sequence => { open = false; onuse(sequence) }} /></div>
  {/if}
</div>

<style>
  .ids-pick{display:contents}
  .ids-open{white-space:nowrap;font-size:13px;padding:6px 10px}
  .ids-open[aria-expanded="true"]{color:var(--accent);border-color:var(--accent)}
  .ids-panel{flex-basis:100%;order:10;max-height:min(60vh,520px);overflow:auto;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
</style>
