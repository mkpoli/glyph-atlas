<script>
  // Frequent runs, shown with the gallery as their text and count, most frequent first, each opening its
  // run's page. `runs` is `[{ text, n }]`, or null while it loads.
  // The list shows `STEP` entries at a time, and starts short again for another list.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'

  let { runs = null, heading = '', tabs = null, failed = '', onretry = () => {} } = $props()
  const id = $props.id()
  const STEP = 24
  let more = $state(0)
  const listed = $derived((runs ?? []).slice(0, STEP * (more + 1)))
  const rest = $derived(Math.max(0, (runs?.length ?? 0) - listed.length))
  $effect.pre(() => { void runs; more = 0 })
</script>

<section class="run-strip" aria-labelledby="{id}-heading">
  <header>
    <h2 id="{id}-heading">{heading}</h2>
    {#if tabs}<div class="run-tabs" role="group" aria-label={t('explore.browseUnit')}>
      {#each tabs.options as [value, text]}<button type="button" aria-pressed={tabs.value === value} onclick={() => tabs.onchange(value)}>{text()}</button>{/each}
    </div>{/if}
  </header>
  {#if failed}<p class="run-status" role="alert">{failed} <button type="button" class="quiet-link" onclick={onretry}>{t('common.tryAgain')}</button></p>
  {:else}
    <ul class="run-chips" aria-busy={!runs}>
      {#if runs}
        {#each listed as run (run.text)}<li><a href={localize(runAddress(run.text))} aria-label={`${run.text} ${t('run.occurrences', { count: run.n })}`}><ReferenceGlyph char={run.text} size="sm" /><small>{number(run.n)}</small></a></li>{/each}
        {#if rest}<li><button type="button" class="run-more" onclick={() => more += 1}>{t('explore.runs.more')}</button></li>{/if}
      {:else}
        {#each Array(12) as _, i (i)}<li><span class="run-placeholder"></span></li>{/each}
      {/if}
    </ul>
  {/if}
</section>

<style>
  .run-strip{margin:4px 0 22px}
  header{display:flex;align-items:center;gap:16px;margin-bottom:10px}
  h2{font-size:13px;font-weight:500;color:var(--ink)}
  .run-tabs{display:flex;gap:2px}
  .run-tabs button{border:0;border-radius:5px;background:transparent;padding:5px 10px;font-size:12px;color:var(--muted)}
  .run-tabs button[aria-pressed="true"]{background:var(--surface-selected);color:var(--ink)}
  ul{list-style:none;margin:0;padding:0}
  .run-chips{display:flex;flex-wrap:wrap;gap:6px}
  .run-chips small{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
  .run-chips a,.run-more{display:flex;align-items:baseline;gap:6px;border:1px solid var(--line);border-radius:999px;background:var(--surface);padding:5px 11px;color:inherit;text-decoration:none;font-size:13px}
  .run-chips a:hover,.run-chips a:focus-visible,.run-more:hover{background:var(--surface-selected)}
  .run-more{font-size:12px;color:var(--muted)}
  .run-placeholder{display:block;width:72px;height:29px;border-radius:999px;background:var(--surface-sunken)}
  .run-status{font-size:12px;color:var(--muted)}
</style>
