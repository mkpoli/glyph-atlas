<script>
  // A pair or trigram among the homepage's crops: one place it was written (`RunImage`), its text and how
  // often it occurs, opening its run's page. `run` is `{ text, n, crops, page, vertical }`; one whose
  // occurrence could not be read has no `crops` and shows its text, and one on its way has no `text` and
  // shows a placeholder. `style` places it in the collage.
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'

  let { run, style = '' } = $props()
</script>

{#if run.text}<a class="glyph-tile run-tile collage-cell" {style} href={localize(runAddress(run.text))} aria-label={`${run.text} · ${t('run.occurrences', { count: run.n })}`}>
  <span class="tile-label"><span class="tile-glyph"><ScriptText text={run.text} titled={false} /></span></span>
  <span class="run-tile-image" aria-hidden="true">{#if run.crops}<RunImage crops={run.crops} page={run.page} vertical={run.vertical} />{:else}<span class="run-tile-text">{run.text}</span>{/if}</span>
  <span class="tile-footer"><span>{t('run.occurrences', { count: run.n })}</span><span class="tile-arrow">↗</span></span>
</a>
{:else}<div class="glyph-skeleton collage-cell" {style} aria-hidden="true"></div>{/if}

<style>
  .run-tile{color:inherit;text-decoration:none}
  .run-tile-image{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:0}
  .run-tile-image :global(svg){max-width:100%;max-height:100%}
  .run-tile-text{font-size:32px;color:var(--faint);writing-mode:vertical-rl;text-orientation:upright}
</style>
