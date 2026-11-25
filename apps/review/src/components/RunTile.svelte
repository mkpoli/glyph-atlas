<script>
  // A pair or trigram among the homepage's crops: one place it was written (`RunImage`), the forms
  // written there with the run's graphemes small after them where they differ, how often it occurs, and
  // the year and work of that place. A run with its occurrence loaded opens that occurrence in context;
  // one whose occurrence could not be read keeps no `crops`, shows its text, and links to the run's page.
  // One on its way has no `text` and shows a placeholder. `style` places it in the collage. In select
  // mode the gallery gives it `selected`, `checked` once every crop it shows is confirmed (a tick after
  // its text), `marks` (its select box) and the handlers of a tile, which it passes on.
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'
  import { workTitle } from '../lib/cropDetails.js'
  import { tileDate } from '../lib/dating.js'
  import { kanaFolded } from '../lib/identity.js'
  import { occurrenceState, occurrenceWritten } from '../lib/sequenceReview.js'

  let { run, style = '', selected = false, checked = false, marks, ...handlers } = $props()
  const detailsId = $props.id()
  // The image's box, so the page around the run fills it as a crop fills its own. The image waits for
  // it, so the run's region is asked for once, in the tile's shape.
  let width = $state(0), height = $state(0)
  // The run's text names its graphemes (にて); its crops say how this place wrote them (𛂌て). A crop the
  // source only transcribed has no written form of its own, and its transcription shows faint.
  const writtenInfo = $derived(occurrenceWritten(run.crops, run.text))
  const forms = $derived(writtenInfo?.forms ?? null)
  const written = $derived(writtenInfo?.written ?? run.text)
  // The graphemes follow small only where the forms are written otherwise; katakana is its own writing.
  const differs = $derived(kanaFolded(written) !== kanaFolded(run.text))
  const named = $derived(differs ? `${written} (${run.text})` : written)
  const state = $derived(occurrenceState(run.crops))
  const isChecked = $derived(checked || state === 'checked')
  const isFlagged = $derived(!isChecked && state === 'flagged')
  const countTip = $derived(run.kind === 'trigram'
    ? t('tile.countTrigram', { text: run.text, count: run.n, rank: run.rank })
    : t('tile.countPair', { text: run.text, count: run.n, rank: run.rank }))
  const aria = $derived(`${named} · ${t('run.occurrences', { count: run.n })}` + (selected ? t('bulk.tileSelected') : '') + (isChecked ? t('bulk.tileChecked') : ''))
</script>

{#snippet inner()}
  {@render marks?.(run)}
  <span id={detailsId} class="tile-details"><span>{countTip}</span></span>
  <span class="run-tile-image" aria-hidden="true" bind:clientWidth={width} bind:clientHeight={height}>{#if run.crops}{#if width && height}<RunImage crops={run.crops} page={run.page} vertical={run.vertical} fill={height / width} />{/if}{:else}<span class="run-tile-text">{run.text}</span>{/if}</span>
  <span class="tile-caption">
    <span class="tile-label"><span class="tile-glyph">{#if forms}{#each forms as form}<span class:transcribed={form.transcribed}><ScriptText text={form.text} titled={false} /></span>{/each}{:else}<ScriptText text={run.text} titled={false} />{/if}</span>{#if differs}<span class="tile-grapheme" title={t('chips.grapheme')}><ScriptText text={run.text} titled={false} /></span>{/if}{#if isChecked || isFlagged}<span class="tile-verdict" aria-hidden="true">{isChecked ? '✓' : '!'}</span>{/if}</span>
    <span class="tile-footer"><span class="tile-count" title={countTip}>{t('tile.count', { count: run.n })}</span>{#if run.crops && tileDate(run.crops[0])}<span class="tile-year">{tileDate(run.crops[0])}</span>{/if}{#if run.crops && workTitle(run.crops[0])}<span class="tile-work">{workTitle(run.crops[0])}</span>{/if}<span class="tile-arrow">↗</span></span>
  </span>
{/snippet}

{#if run.text}
  {#if run.crops?.length}
    <button type="button" class="glyph-tile run-tile collage-cell" class:selected class:decided-checked={isChecked} class:decided-flagged={isFlagged} {style} data-run={run.id} aria-label={aria} aria-describedby={detailsId} {...handlers}>
      {@render inner()}
    </button>
  {:else}
    <a class="glyph-tile run-tile collage-cell" class:selected class:decided-checked={isChecked} class:decided-flagged={isFlagged} {style} data-run={run.id} href={localize(runAddress(run.text))} aria-label={aria} aria-describedby={detailsId} {...handlers}>
      {@render inner()}
    </a>
  {/if}
{:else}<div class="glyph-skeleton collage-cell" {style} aria-hidden="true"></div>{/if}

<style>
  .run-tile{color:inherit;text-decoration:none;text-align:inherit;font:inherit}
  .transcribed{opacity:.5}
  .run-tile.decided-checked{background:var(--good-light)}
  .run-tile.decided-flagged{background:var(--wrong-light)}
  .run-tile.decided-checked::after,.run-tile.decided-flagged::after{content:'';position:absolute;inset:0;z-index:3;border:2px solid var(--good);pointer-events:none}
  .run-tile.decided-flagged::after{border-color:var(--wrong)}
  .run-tile-image{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:0}
  .run-tile-image :global(svg){max-width:100%;max-height:100%}
  .tile-count{white-space:nowrap}
  .tile-year{white-space:nowrap;font-variant-numeric:tabular-nums}
  .tile-work{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .run-tile-text{font-size:32px;color:var(--faint);writing-mode:vertical-rl;text-orientation:upright}
</style>
