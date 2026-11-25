<script>
  // Crop and review counts by script group (`all`, `kana`, `kanji`, `hangul`, `gugyeol`, `tangut`, `seal`, `other`)
  // inside the collection dialog, combining collected crops and corpus glyphs (`All` / `Collected` / `Corpus`).
  import { onMount } from 'svelte'
  import { t, formatNumber } from '../lib/i18n.svelte.js'

  let stats = $state(null), loading = $state(true), failed = $state(false), scope = $state('all')
  let controller = null

  const COLORS = {
    kana: 'var(--script-hiragana)',
    kanji: 'var(--script-kanji)',
    hangul: 'var(--script-hangul)',
    gugyeol: 'var(--script-gugyeol)',
    tangut: 'var(--script-tangut)',
    seal: 'var(--script-seal)',
    other: 'var(--script-unknown)',
  }

  async function read() {
    controller?.abort()
    controller = new AbortController()
    loading = true; failed = false
    try {
      const response = await fetch('/atlas/groups/stats', {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        headers: { accept: 'application/json' },
      })
      if (!response.ok) throw new Error('status')
      stats = await response.json()
    } catch (e) {
      if (e.name !== 'AbortError') failed = true
    } finally {
      loading = false
    }
  }

  onMount(() => {
    read()
    return () => controller?.abort()
  })

  const groups = $derived(stats?.groups ?? [])
  const allEntry = $derived(groups.find(entry => entry.group === 'all') ?? null)
  const hasBothScopes = $derived(Boolean(allEntry && allEntry.collected?.total > 0 && allEntry.corpus?.total > 0))
  const shownScope = $derived(hasBothScopes ? scope : 'all')
  const metrics = entry => (shownScope === 'all' ? entry : entry[shownScope])
  const visible = $derived(groups.filter(entry => entry.group !== 'other' || entry.total > 0))
  const totalForShare = $derived(allEntry ? metrics(allEntry).total : 0)
  const allMetrics = $derived(allEntry ? metrics(allEntry) : null)
  const hasOther = $derived(visible.some(entry => entry.group === 'other'))

  const label = group => group === 'other' ? t('progress.scripts.other') : t(`explore.filter.${group}`)
  const pct = (part, whole) => {
    if (!whole || !part) return ''
    const ratio = part / whole * 100
    return ratio < 0.1 ? '<0.1%' : ratio < 10 ? `${ratio.toFixed(1)}%` : `${Math.round(ratio)}%`
  }
</script>

<section class="script-stats" aria-labelledby="script-stats-title">
  <div class="script-heading">
    <h3 id="script-stats-title">{t('progress.scripts.heading')}</h3>
    {#if hasBothScopes}
      <div class="measure" role="group" aria-label={t('progress.scripts.source')}>
        <button type="button" class:active={scope === 'all'} aria-pressed={scope === 'all'} onclick={() => scope = 'all'}>{t('progress.scripts.allSources')}</button>
        <button type="button" class:active={scope === 'collected'} aria-pressed={scope === 'collected'} onclick={() => scope = 'collected'}>{t('progress.scripts.collected')}</button>
        <button type="button" class:active={scope === 'corpus'} aria-pressed={scope === 'corpus'} onclick={() => scope = 'corpus'}>{t('progress.scripts.corpus')}</button>
      </div>
    {/if}
  </div>

  {#if loading && !stats}
    <p class="script-note" role="status">{t('progress.scripts.loading')}</p>
  {:else if failed && !stats}
    <p class="script-error" role="alert">{t('progress.scripts.failed')} <button type="button" onclick={read}>{t('common.retry')}</button></p>
  {:else if visible.length}
    <table class="script-table">
      <thead>
        <tr>
          <th scope="col">{t('progress.scripts.group')}</th>
          <th scope="col">{t('progress.scripts.total')}</th>
          <th scope="col">{t('progress.scripts.reviewed')}</th>
          <th scope="col">{t('progress.scripts.checked')}</th>
          <th scope="col">{t('progress.scripts.needFixing')}</th>
          <th scope="col">{t('progress.scripts.pending')}</th>
        </tr>
      </thead>
      <tbody>
        {#each visible as entry (entry.group)}
          {@const m = metrics(entry)}
          {@const reviewedShare = pct(m.reviewed, m.total)}
          {@const shareWidth = entry.group !== 'all' && totalForShare ? (m.total ? Math.max(1.5, m.total / totalForShare * 100) : 0) : 0}
          <tr class:total-row={entry.group === 'all'} class:empty-row={m.total === 0}>
            <th scope="row">
              <span class="script-name">
                {#if COLORS[entry.group]}<i class="script-dot" style:background={COLORS[entry.group]} aria-hidden="true"></i>{/if}
                <span>{label(entry.group)}</span>
              </span>
              {#if entry.group !== 'all'}
                <span class="share-track" aria-hidden="true"><span style:width="{shareWidth}%" style:background={COLORS[entry.group]}></span></span>
              {/if}
            </th>
            <td><span class="cell-label" aria-hidden="true">{t('progress.scripts.total')}</span><span class="cell-value">{formatNumber(m.total)}</span></td>
            <td><span class="cell-label" aria-hidden="true">{t('progress.scripts.reviewed')}</span><span class="cell-value">{formatNumber(m.reviewed)}{#if reviewedShare}<small>({reviewedShare})</small>{/if}</span></td>
            <td><span class="cell-label" aria-hidden="true">{t('progress.scripts.checked')}</span><span class="cell-value">{formatNumber(m.checked)}</span></td>
            <td class:has-issues={m.need_fixing > 0}><span class="cell-label" aria-hidden="true">{t('progress.scripts.needFixing')}</span><span class="cell-value">{formatNumber(m.need_fixing)}</span></td>
            <td><span class="cell-label" aria-hidden="true">{t('progress.scripts.pending')}</span><span class="cell-value">{formatNumber(m.pending + m.seen)}</span></td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if allMetrics}
      {#if shownScope === 'all' && hasBothScopes}
        <p class="script-meta">{t('progress.scripts.sourcesBreakdown', { collected: allEntry.collected.total, corpus: allEntry.corpus.total })}</p>
      {/if}
      {#if allMetrics.seen > 0 || allMetrics.withheld > 0}
        <p class="script-meta">
          {#if allMetrics.seen > 0}<span>{t('progress.scripts.seen', { count: allMetrics.seen })}</span>{/if}
          {#if allMetrics.seen > 0 && allMetrics.withheld > 0}<span class="meta-dot">·</span>{/if}
          {#if allMetrics.withheld > 0}<span>{t('progress.scripts.withheld', { count: allMetrics.withheld })}</span>{/if}
        </p>
      {/if}
      {#if hasOther}<p class="script-note">{t('progress.scripts.otherNote')}</p>{/if}
    {/if}
  {/if}
</section>

<style>
  .script-stats{margin-top:22px;padding:18px;border:1px solid var(--line);border-radius:12px}
  .script-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
  h3{font-size:15px;font-weight:500;margin:0}
  .measure{display:flex;border:1px solid var(--line);border-radius:7px;padding:2px}
  .measure button{border:0;background:transparent;border-radius:5px;padding:3px 9px;font-size:11px;color:var(--muted)}
  .measure button.active{background:var(--surface-selected);color:var(--ink)}
  .script-table{width:100%;border-collapse:collapse;margin-top:14px;font-size:12px;font-variant-numeric:tabular-nums}
  .script-table th,.script-table td{padding:7px 4px;text-align:right;vertical-align:middle}
  .script-table thead th{font-size:10px;font-weight:500;color:var(--muted);border-bottom:1px solid var(--line);padding-top:0}
  .script-table thead th:first-child,.script-table tbody th{text-align:left;padding-left:0}
  .script-table tbody td:last-child,.script-table thead th:last-child{padding-right:0}
  .script-table tbody th{font-weight:400;min-width:86px}
  .script-table tbody tr+tr th,.script-table tbody tr+tr td{border-top:1px solid var(--line)}
  .script-table tbody tr.total-row th,.script-table tbody tr.total-row td{font-weight:500;padding-bottom:9px;border-bottom:1px solid var(--line-strong)}
  .script-table tbody tr.empty-row{color:var(--muted)}
  .script-name{display:inline-flex;align-items:center;gap:6px}
  .script-dot{width:7px;height:7px;border-radius:50%;flex:none}
  .share-track{display:block;height:2px;margin-top:4px;background:light-dark(#ececed, #2e2e35);border-radius:999px;overflow:hidden;max-width:74px}
  .share-track span{display:block;height:100%;opacity:.75;border-radius:999px}
  .cell-label{display:none}
  .cell-value small{margin-left:4px;font-size:10px;color:var(--muted);font-weight:400}
  .has-issues{color:var(--wrong)}
  .script-meta{margin:10px 0 0;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;display:flex;flex-wrap:wrap;gap:4px}
  .meta-dot{margin:0 2px}
  .script-note{margin:8px 0 0;font-size:11px;color:var(--faint);line-height:1.5}
  .script-error{margin:10px 0 0;font-size:12px;color:light-dark(#a3341f, #ffab94)}
  .script-error button{font:inherit;text-decoration:underline;background:none;border:0;color:inherit;cursor:pointer;padding:0 4px}
  @media(max-width:560px){
    .script-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
    .script-table,.script-table tbody{display:block}
    .script-table tbody tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 14px;padding:10px 0;border-bottom:1px solid var(--line)}
    .script-table tbody tr+tr th,.script-table tbody tr+tr td{border-top:0}
    .script-table tbody tr.total-row td{border-bottom:0;padding-bottom:2px}
    .script-table tbody tr.total-row th{border-bottom:0;padding-bottom:4px}
    .script-table tbody tr.total-row{border-bottom:1px solid var(--line-strong)}
    .script-table tbody th{grid-column:1/-1;padding:0 0 4px}
    .script-table tbody td{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding:2px 0;text-align:right}
    .cell-label{display:inline;font-size:11px;color:var(--muted);font-weight:400}
  }
</style>
