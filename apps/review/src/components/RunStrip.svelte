<script module>
  import { runOccurrences } from '../lib/ngrams.js'
  // One occurrence of each run, read once a visit and kept for every strip that shows the run. It is the
  // run's second (`offset: 1`): the first page of a run also counts all its occurrences, which the
  // strip has no use for, and the runs it shows have thousands. A run with one occurrence shows its text.
  const occurrences = new Map()
  function occurrence(text) {
    if (!occurrences.has(text)) occurrences.set(text, runOccurrences(text, { offset: 1, limit: 1 })
      .then(page => page.items?.[0] ?? null, () => { occurrences.delete(text); return null }))
    return occurrences.get(text)
  }
</script>

<script>
  // Frequent runs, shown with the gallery: the first few as one of their occurrences on the page (the
  // crops in place, `RunImage`), the rest as their text, each opening its run's page. `runs` is
  // `[{ text, n, vertical }]`, most frequent first, or null while it loads. An occurrence is read only
  // once the strip comes near the screen; until then, and for a run whose occurrence cannot be read,
  // its card keeps its size and shows the run's text in place of the crops. The list shows `STEP` runs
  // more at a time, and starts short again for another list.
  import RunImage from './RunImage.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress } from '../lib/ngrams.js'

  let { runs = null, heading = '', tabs = null, failed = '', onretry = () => {} } = $props()
  const id = $props.id()
  /** How many runs are drawn with their crops, and how many more as text, at first and per "More". */
  const PICTURED = 6, STEP = 12
  let more = $state(0), strip = $state(), near = $state(false), seen = $state({})
  const pictured = $derived((runs ?? []).slice(0, PICTURED))
  const listed = $derived((runs ?? []).slice(PICTURED, PICTURED + STEP * (more + 1)))
  const rest = $derived(Math.max(0, (runs?.length ?? 0) - PICTURED - listed.length))
  $effect.pre(() => { void runs; more = 0 })

  $effect(() => {
    if (!strip || near) return
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) near = true }, { rootMargin: '300px' })
    observer.observe(strip)
    return () => observer.disconnect()
  })
  let closed = false
  $effect(() => () => { closed = true })
  $effect(() => {
    if (!near) return
    for (const { text } of pictured) occurrence(text).then(found => { if (!closed) seen[text] = found })
  })
</script>

<section class="run-strip" bind:this={strip} aria-labelledby="{id}-heading">
  <header>
    <h2 id="{id}-heading">{heading}</h2>
    {#if tabs}<div class="run-tabs" role="group" aria-label={t('explore.browseUnit')}>
      {#each tabs.options as [value, text]}<button type="button" aria-pressed={tabs.value === value} onclick={() => tabs.onchange(value)}>{text()}</button>{/each}
    </div>{/if}
  </header>
  {#if failed}<p class="run-status" role="alert">{failed} <button type="button" class="quiet-link" onclick={onretry}>{t('common.tryAgain')}</button></p>
  {:else}
    <ul class="run-cards" aria-busy={!runs}>
      {#each runs ? pictured : Array(PICTURED).fill(null) as run, i (run?.text ?? i)}
        <li>{#if run}<a href={localize(runAddress(run.text))} aria-label={`${run.text} ${t('run.occurrences', { count: run.n })}`}>
          <span class="run-card-image" aria-hidden="true">{#if seen[run.text]}<RunImage crops={seen[run.text].crops} page={seen[run.text].page} vertical={seen[run.text].vertical} />{:else}<span class="run-card-text" class:vertical={run.vertical}>{run.text}</span>{/if}</span>
          <span class="run-card-caption"><ReferenceGlyph char={run.text} size="sm" /><small>{number(run.n)}</small></span>
        </a>{:else}<span class="run-card-placeholder"></span>{/if}</li>
      {/each}
    </ul>
    {#if listed.length}<ul class="run-chips">
      {#each listed as run (run.text)}<li><a href={localize(runAddress(run.text))} aria-label={`${run.text} ${t('run.occurrences', { count: run.n })}`}><ReferenceGlyph char={run.text} size="sm" /><small>{number(run.n)}</small></a></li>{/each}
      {#if rest}<li><button type="button" class="run-more" onclick={() => more += 1}>{t('explore.runs.more')}</button></li>{/if}
    </ul>{/if}
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
  .run-cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}
  .run-cards a,.run-card-placeholder{display:flex;flex-direction:column;height:178px;border-radius:8px;background:var(--surface-tile);color:inherit;text-decoration:none;overflow:hidden}
  .run-card-placeholder{background:var(--surface-sunken)}
  .run-cards a:hover,.run-cards a:focus-visible{background:var(--accent-tile)}
  .run-card-image{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:10px 10px 0}
  .run-card-text{font-size:26px;color:var(--faint)}
  .run-card-text.vertical{writing-mode:vertical-rl;text-orientation:upright}
  .run-card-caption{display:flex;align-items:baseline;justify-content:space-between;gap:6px;padding:6px 10px 8px;font-size:13px}
  .run-card-caption small,.run-chips small{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
  .run-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .run-chips a,.run-more{display:flex;align-items:baseline;gap:6px;border:1px solid var(--line);border-radius:999px;background:var(--surface);padding:5px 11px;color:inherit;text-decoration:none;font-size:13px}
  .run-chips a:hover,.run-chips a:focus-visible,.run-more:hover{background:var(--surface-selected)}
  .run-more{font-size:12px;color:var(--muted)}
  .run-status{font-size:12px;color:var(--muted)}
  @media(max-width:900px){.run-cards{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media(max-width:700px){.run-cards{gap:6px}.run-cards a,.run-card-placeholder{height:150px}}
</style>
