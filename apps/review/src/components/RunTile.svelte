<script>
  // A pair or trigram among the homepage's crops: one place it was written (`RunImage`), its text, how
  // often it occurs, and the year and work of that place, opening its run's page. `run` is
  // `{ text, n, crops, page, vertical }`; one whose occurrence could not be read has no `crops` and shows
  // its text, and one on its way has no `text` and shows a placeholder. `style` places it in the collage.
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'
  import { workTitle } from '../lib/cropDetails.js'
  import { tileDate } from '../lib/dating.js'
  import { number } from '../lib/client.js'

  let { run, style = '' } = $props()
  // The image's box, so the page around the run fills it as a crop fills its own. The image waits for
  // it, so the run's region is asked for once, in the tile's shape.
  let width = $state(0), height = $state(0)
</script>

{#if run.text}<a class="glyph-tile run-tile collage-cell" {style} href={localize(runAddress(run.text))} aria-label={`${run.text} · ${t('run.occurrences', { count: run.n })}`}>
  <span class="run-tile-image" aria-hidden="true" bind:clientWidth={width} bind:clientHeight={height}>{#if run.crops}{#if width && height}<RunImage crops={run.crops} page={run.page} vertical={run.vertical} fill={height / width} />{/if}{:else}<span class="run-tile-text">{run.text}</span>{/if}</span>
  <span class="tile-caption">
    <span class="tile-label"><span class="tile-glyph"><ScriptText text={run.text} titled={false} /></span></span>
    <span class="tile-footer"><span class="tile-count">{number(run.n)}</span>{#if run.crops && tileDate(run.crops[0])}<span class="tile-year">{tileDate(run.crops[0])}</span>{/if}{#if run.crops && workTitle(run.crops[0])}<span class="tile-work">{workTitle(run.crops[0])}</span>{/if}<span class="tile-arrow">↗</span></span>
  </span>
</a>
{:else}<div class="glyph-skeleton collage-cell" {style} aria-hidden="true"></div>{/if}

<style>
  .run-tile{color:inherit;text-decoration:none}
  .run-tile-image{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:0}
  .run-tile-image :global(svg){max-width:100%;max-height:100%}
  .tile-count{white-space:nowrap}
  .tile-year{white-space:nowrap;font-variant-numeric:tabular-nums}
  .tile-work{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .run-tile-text{font-size:32px;color:var(--faint);writing-mode:vertical-rl;text-orientation:upright}
</style>
