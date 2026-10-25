<script>
  // A pair or trigram among the homepage's crops: one place it was written (`RunImage`), the forms
  // written there with the run's graphemes small after them where they differ, how often it occurs, and the year and work of that place, opening its run's page. `run` is
  // `{ text, n, kind, rank, crops, page, vertical }`, `rank` its place among its kind's counts; one
  // whose occurrence could not be read has no `crops` and shows its text, and one on its way has no
  // `text` and shows a placeholder. `style` places it in the collage. In select mode the gallery gives it
  // `selected`, `checked` once every crop it shows is confirmed (a tick after its text), `marks` (its select box) and
  // the handlers of a tile, which it passes on.
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'
  import { workTitle } from '../lib/cropDetails.js'
  import { tileDate } from '../lib/dating.js'
  import { isUnassigned } from '../lib/identity.js'

  let { run, style = '', selected = false, checked = false, marks, ...handlers } = $props()
  // The image's box, so the page around the run fills it as a crop fills its own. The image waits for
  // it, so the run's region is asked for once, in the tile's shape.
  let width = $state(0), height = $state(0)
  // The run's text names its graphemes (にて); its crops say how this place wrote them (𛂌て). A crop the
  // source only transcribed has no written form of its own, and its transcription shows faint.
  const forms = $derived(run.crops?.length ? run.crops.map(crop => ({ text: crop.written_character ?? crop.label ?? '', transcribed: isUnassigned(crop) })) : null)
  const written = $derived(forms ? forms.map(form => form.text).join('') : run.text)
  const named = $derived(written === run.text ? run.text : `${written} (${run.text})`)
</script>

{#if run.text}<a class="glyph-tile run-tile collage-cell" class:selected class:decided-checked={checked} {style} data-run={run.id} href={localize(runAddress(run.text))} aria-label={`${named} · ${t('run.occurrences', { count: run.n })}` + (selected ? t('bulk.tileSelected') : '') + (checked ? t('bulk.tileChecked') : '')} {...handlers}>
  {@render marks?.(run)}
  <span class="run-tile-image" aria-hidden="true" bind:clientWidth={width} bind:clientHeight={height}>{#if run.crops}{#if width && height}<RunImage crops={run.crops} page={run.page} vertical={run.vertical} fill={height / width} />{/if}{:else}<span class="run-tile-text">{run.text}</span>{/if}</span>
  <span class="tile-caption">
    <span class="tile-label"><span class="tile-glyph">{#if forms}{#each forms as form}<span class:transcribed={form.transcribed}><ScriptText text={form.text} titled={false} /></span>{/each}{:else}<ScriptText text={run.text} titled={false} />{/if}</span>{#if written !== run.text}<span class="tile-grapheme" title={t('chips.grapheme')}><ScriptText text={run.text} titled={false} /></span>{/if}{#if checked}<span class="tile-verdict" aria-hidden="true">✓</span>{/if}</span>
    <span class="tile-footer"><span class="tile-count" title={run.kind === 'trigram' ? t('tile.countTrigram', { text: run.text, count: run.n, rank: run.rank }) : t('tile.countPair', { text: run.text, count: run.n, rank: run.rank })}>{t('tile.count', { count: run.n })}</span>{#if run.crops && tileDate(run.crops[0])}<span class="tile-year">{tileDate(run.crops[0])}</span>{/if}{#if run.crops && workTitle(run.crops[0])}<span class="tile-work">{workTitle(run.crops[0])}</span>{/if}<span class="tile-arrow">↗</span></span>
  </span>
</a>
{:else}<div class="glyph-skeleton collage-cell" {style} aria-hidden="true"></div>{/if}

<style>
  .run-tile{color:inherit;text-decoration:none}
  .transcribed{opacity:.5}
  .run-tile.decided-checked{background:var(--good-light)}
  .run-tile.decided-checked::after{content:'';position:absolute;inset:0;z-index:3;border:2px solid var(--good);pointer-events:none}
  .run-tile-image{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:0}
  .run-tile-image :global(svg){max-width:100%;max-height:100%}
  .tile-count{white-space:nowrap}
  .tile-year{white-space:nowrap;font-variant-numeric:tabular-nums}
  .tile-work{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .run-tile-text{font-size:32px;color:var(--faint);writing-mode:vertical-rl;text-orientation:upright}
</style>
