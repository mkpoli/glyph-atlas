<script>
  // The collection's dates in numbers, inside the stats dialog: how much of it is dated, how the dated
  // crops or works fall by hundred years and by decade of the year their copy's date begins in, and what
  // those dates date. `/atlas/dates/stats` counts them from the Worker's cached counts.
  import { onMount } from 'svelte'
  import { t, formatNumber, formatYear } from '../lib/i18n.svelte.js'
  let stats = $state(null), measure = $state('crops')
  onMount(() => {
    const controller = new AbortController()
    fetch('/atlas/dates/stats', { signal: controller.signal, headers: { accept: 'application/json' } })
      .then(response => response.ok ? response.json() : null).then(found => { stats = found }).catch(() => {})
    return () => controller.abort()
  })
  const at = $derived(measure === 'crops' ? 1 : 2)
  const percent = (part, whole) => whole ? Math.round(part / whole * 100) : 0
  const hundreds = $derived(stats?.hundreds ?? [])
  const most = $derived(Math.max(1, ...hundreds.map(row => row[at])))
  // The decades on one line to scale, from the first hundred years with a date to the last.
  const from = $derived(hundreds.length ? hundreds[0][0] : 0), to = $derived(hundreds.length ? hundreds.at(-1)[0] + 100 : 0)
  const decadeMost = $derived(Math.max(1, ...(stats?.decades ?? []).map(row => row[at])))
  const share = year => (year - from) / Math.max(1, to - from) * 100
  const kindMost = $derived(Math.max(1, ...(stats?.kinds ?? []).map(row => row[at])))
  const step = $derived(Math.max(1, Math.ceil(hundreds.length / 6)))
</script>

{#if stats?.total?.crops}
  <section class="date-stats" aria-labelledby="date-stats-title">
    <div class="date-heading">
      <h3 id="date-stats-title">{t('progress.dates.heading')}</h3>
      <div class="measure" role="group" aria-label={t('progress.dates.measure')}>
        <button class:active={measure === 'crops'} aria-pressed={measure === 'crops'} onclick={() => measure = 'crops'}>{t('progress.dates.crops')}</button>
        <button class:active={measure === 'works'} aria-pressed={measure === 'works'} onclick={() => measure = 'works'}>{t('progress.dates.works')}</button>
      </div>
    </div>
    <div class="share" role="img" aria-label={t('progress.dates.share', { crops: percent(stats.dated.crops, stats.total.crops), works: percent(stats.dated.works, stats.total.works) })}>
      <span style:width="{percent(stats.dated[measure], stats.total[measure])}%"></span>
    </div>
    <p class="share-text">{t('progress.dates.share', { crops: percent(stats.dated.crops, stats.total.crops), works: percent(stats.dated.works, stats.total.works) })}</p>
    <dl class="share-counts">
      <div><dt>{t('progress.dates.dated')}</dt><dd>{formatNumber(stats.dated[measure])}</dd></div>
      <div><dt>{t('date.noYear')}</dt><dd>{formatNumber(stats.total[measure] - stats.dated[measure])}</dd></div>
      <div><dt>{t('progress.dates.composed')}</dt><dd>{formatNumber(stats.composed[measure])}</dd></div>
    </dl>

    {#if hundreds.length}
      <h4>{t('progress.dates.hundreds')}</h4>
      <div class="hundreds">
        {#each hundreds as row (row[0])}
          <div class="column" title={`${formatYear(row[0])}–${formatYear(row[0] + 99)}: ${formatNumber(row[at])}`}>
            <span class="value">{formatNumber(row[at])}</span>
            <span class="pillar" style:height="{Math.max(2, row[at] / most * 64)}px"></span>
          </div>
        {/each}
      </div>
      <div class="hundred-labels" aria-hidden="true">
        {#each hundreds as row, i (row[0])}<span>{i % step === 0 ? formatYear(row[0]) : ''}</span>{/each}
      </div>
      <h4>{t('progress.dates.decades')}</h4>
      <div class="decades" role="img" aria-label={t('progress.dates.decades')}>
        {#each stats.decades as row (row[0])}<span style:left="{share(row[0])}%" style:width="{share(from + 10)}%" style:height="{Math.max(1, Math.sqrt(row[at] / decadeMost) * 28)}px"
          title={`${t('date.decade', { year: formatYear(row[0]) })}: ${formatNumber(row[at])}`}></span>{/each}
      </div>
      <div class="decade-labels" aria-hidden="true"><span>{formatYear(from)}</span><span>{formatYear(to)}</span></div>
    {/if}

    {#if stats.kinds.length}
      <h4>{t('progress.dates.kinds')}</h4>
      <dl class="kinds">
        {#each stats.kinds as row (row[0])}
          <div><dt>{t(`progress.dates.kind.${row[0]}`)}</dt><dd><span class="kind-bar" style:width="{row[at] / kindMost * 100}%"></span><b>{formatNumber(row[at])}</b></dd></div>
        {/each}
      </dl>
    {/if}
    <p class="date-note">{t('progress.dates.note')}</p>
  </section>
{/if}

<style>
  .date-stats{margin-top:22px;padding:18px;border:1px solid var(--line);border-radius:12px}
  .date-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
  h3{font-size:15px;font-weight:500;margin:0}
  h4{margin:18px 0 8px;font-size:11px;font-weight:500;color:var(--muted)}
  .measure{display:flex;border:1px solid var(--line);border-radius:7px;padding:2px}
  .measure button{border:0;background:transparent;border-radius:5px;padding:3px 9px;font-size:11px;color:var(--muted)}
  .measure button.active{background:var(--surface-selected);color:var(--ink)}
  .share{height:4px;border-radius:999px;background:light-dark(#ececed, #2e2e35);overflow:hidden;margin-top:14px}
  .share span{display:block;height:100%;background:var(--accent)}
  .share-text{margin:8px 0 0;font-size:12px;color:var(--muted)}
  .share-counts,.kinds{margin:10px 0 0;display:grid;gap:6px}
  .share-counts div,.kinds div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;font-size:12px}
  dt{color:var(--muted)}dd{margin:0;font-variant-numeric:tabular-nums}
  .hundreds{display:flex;align-items:flex-end;gap:3px;height:86px}
  .column{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:3px}
  .value{font-size:9px;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap}
  .pillar{width:100%;max-width:22px;border-radius:2px 2px 0 0;background:var(--accent);opacity:.7}
  .hundred-labels{display:flex;gap:3px;border-top:1px solid var(--line-strong);padding-top:4px}
  .hundred-labels span{flex:1 1 0;min-width:0;font-size:9px;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:visible}
  .decades{position:relative;height:30px;border-bottom:1px solid var(--line-strong)}
  .decades span{position:absolute;bottom:0;min-width:1px;background:var(--accent);opacity:.6}
  .decade-labels{display:flex;justify-content:space-between;font-size:9px;color:var(--faint);padding-top:4px;font-variant-numeric:tabular-nums}
  .kinds dd{display:flex;align-items:center;gap:8px;flex:0 0 55%}
  .kind-bar{height:4px;border-radius:999px;background:var(--accent);opacity:.55}
  .kinds b{font-weight:400;margin-left:auto}
  .date-note{margin:14px 0 0;font-size:11px;color:var(--faint);line-height:1.5}
</style>
