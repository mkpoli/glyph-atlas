<script>
  import { onMount } from 'svelte'
  import { t, formatNumber as number, formatDateTime, around } from '../lib/i18n.svelte.js'
  let { close } = $props()
  /** A click on the backdrop closes the dialog. The backdrop has no element of its own, so a click on
   * it lands on the dialog; its position outside the dialog's box is what tells it apart. */
  function outside(event) {
    const box = dialog.getBoundingClientRect()
    if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right
        || event.clientY < box.top || event.clientY > box.bottom)) close()
  }
  let dialog, data = $state(null), error = $state(''), loading = $state(true)
  const controller = new AbortController()
  const phases = { discovering: () => t('progress.phase.discovering'), collecting: () => t('progress.phase.collecting'),
    publishing: () => t('progress.phase.publishing'), complete: () => t('progress.phase.complete') }
  const clock = value => value ? formatDateTime(value, { date: false }) : ''
  const percent = source => source.total ? Math.min(100, Math.round(source.completed / source.total * 100)) : 0

  async function read() {
    try {
      const response = await fetch('/atlas/collection/status', {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        redirect: 'error', headers: { accept: 'application/json' },
      })
      if (!response.ok) throw new Error(t('progress.readError', { status: response.status }))
      data = await response.json(); error = ''
    } catch (e) {
      if (e.name !== 'AbortError') error = e.name === 'TimeoutError' ? t('progress.timedOut') : e.message
    } finally { loading = false }
  }

  onMount(() => {
    dialog.showModal(); read()
    const timer = setInterval(read, 15000)
    return () => { clearInterval(timer); controller.abort() }
  })
</script>

<dialog class="progress-dialog" bind:this={dialog} oncancel={close} onclick={outside} aria-labelledby="progress-title">
  <div class="progress-heading">
    <h2 id="progress-title">{t('progress.title')}</h2>
    <button class="icon-button" aria-label={t('progress.close')} onclick={close}>×</button>
  </div>
  {#if error}<p class="progress-error" role="alert">{error} <button onclick={read}>{t('common.tryAgain')}</button></p>{/if}
  {#if loading && !data}<p class="progress-note" role="status">{t('progress.loading')}</p>{/if}
  {#if data}
    {#if data.archive}
      <div class="archive-counts">
        <p><strong>{number(data.archive.character_crops)}</strong><span>{t('progress.archive.crops')}</span></p>
        <p><strong>{number(data.archive.works_with_crops)}</strong><span>{t('progress.archive.works')}</span></p>
      </div>
      <p class="progress-note">{t('progress.archive.textWorks', { count: data.archive.text_works })}</p>
    {/if}
    <div class="sources">
      {#if data.extraction}
        <section class="source extraction" aria-label={t('progress.extraction.label')}>
          <div class="source-heading"><h3>{t('progress.extraction.heading')}</h3><span class="state">{data.extraction.state === 'running' ? t('progress.state.extracting') : data.extraction.state?.startsWith('paused') ? t('progress.state.paused') : t('progress.state.waiting')}</span></div>
          <dl class="stages">
            <div><dt>{t('progress.extraction.publishedCrops')}</dt><dd>{number(data.extraction.published_crops)}</dd></div>
            <div><dt>{t('progress.extraction.booksWithCrops')}</dt><dd>{number(data.extraction.books_with_crops)}</dd></div>
            <div><dt>{t('progress.extraction.pagesExtracted')}</dt><dd>{number(data.extraction.counts?.complete)}</dd></div>
            <div><dt>{t('progress.extraction.pagesQueued')}</dt><dd>{number((data.extraction.counts?.pending ?? 0) + (data.extraction.counts?.running ?? 0) + (data.extraction.counts?.retry ?? 0))}</dd></div>
          </dl>
          {#if data.extraction.counts?.failed}<p class="progress-note">{t('progress.extraction.needAnotherPass', { count: data.extraction.counts.failed })}</p>{/if}
          <p class="progress-note">{t('progress.extraction.fromTranscriptions')}</p>
        </section>
      {/if}
      {#each data.sources ?? [data] as source}
        <section class="source" aria-label={t('progress.source.label', { name: source.name ?? 'Honkoku' })}>
          <div class="source-heading">
            <h3>{source.name ?? 'Honkoku'}</h3>
            <span class:paused={source.status === 'paused'} class="state">
              {#if source.status === 'running'}<i class="live-dot"></i>{phases[source.phase]?.() ?? source.phase}
              {:else if source.status === 'waiting'}{t('progress.state.waiting')}
              {:else if source.status === 'paused'}{t('progress.state.paused')}
              {:else if source.phase === 'complete'}{t('progress.phase.complete')}
              {:else}{t('progress.state.stopped')}{/if}
            </span>
          </div>
          {#if source.discovery_complete}
            <div class="bar" role="img" aria-label={t('progress.bar.percentCollected', { percent: percent(source) })}><span style={`width:${percent(source)}%`}></span></div>
            <p class="book-count">{around('progress.worksCollected', 'completed', { total: source.total })[0]}<strong>{number(source.completed)}</strong>{around('progress.worksCollected', 'completed', { total: source.total })[1]}</p>
          {:else}
            <div class="bar discovering" role="img" aria-label={t('progress.bar.discovering')}><span></span></div>
            <p class="book-count">{around('progress.worksFound', 'total')[0]}<strong>{number(source.total)}</strong>{around('progress.worksFound', 'total')[1]}</p>
            {#if source.discovered_pages}<p class="progress-note">{t('progress.pagesFound', { count: source.discovered_pages })}</p>{/if}
          {/if}
          <dl class="stages">
            <div><dt>{t('progress.source.publishedBooks')}</dt><dd>{number(source.published_books)}</dd></div>
            <div><dt>{t('progress.source.textPages')}</dt><dd>{number(source.text_pages)}</dd></div>
            <div><dt>{t('progress.source.characterCrops')}</dt><dd>{number(source.character_crops)}</dd></div>
          </dl>
          {#if source.import_geometry === 'page_text_only'}<p class="progress-note alignment">{t('progress.source.alignmentNeeded')}</p>{/if}
          {#if source.current}<p class="current" title={source.current.title}>{source.current.title ?? source.current.id}</p>{/if}
          {#if source.status === 'paused' && source.pause_reason}<p class="progress-note paused">{source.pause_reason}</p>
          {:else if source.next_at}<p class="progress-note">{t('progress.source.nextWorkAt', { time: clock(source.next_at) })}</p>{/if}
          <div class="source-foot">
            <span>{source.failed ? t('progress.source.unavailable', { count: source.failed }) : t('progress.source.pauseSeconds', { seconds: source.pause_seconds ?? 60 })}</span>
            <span>{source.updated_at ? t('progress.source.updatedAt', { time: clock(source.updated_at) }) : ''}</span>
          </div>
        </section>
      {/each}
    </div>
    {#if data.additions?.length}
      <ul class="additions">
        {#each data.additions.slice(0, 3) as addition}<li><span>{addition.title}</span><small>{t('progress.additions.scanReferences', { count: addition.pages })}</small></li>{/each}
      </ul>
    {/if}
  {/if}
</dialog>

<style>
  .progress-dialog{width:min(500px,calc(100vw - 32px));max-height:calc(100dvh - 32px);margin:auto;padding:26px 28px;border:1px solid var(--line);border-radius:16px;color:var(--ink);background:var(--surface);box-shadow:0 24px 80px var(--shadow);overflow:auto}
  .progress-dialog::backdrop{background:var(--backdrop);backdrop-filter:blur(3px)}
  .progress-heading,.source-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
  h2{font-size:22px;font-weight:500;letter-spacing:-.4px;margin:0}h3{font-size:15px;font-weight:500;margin:0}
  .archive-counts{display:grid;grid-template-columns:1.2fr 1fr;gap:20px;margin-top:22px}
  .archive-counts p{margin:0;display:flex;flex-direction:column;gap:4px}.archive-counts strong{font-size:28px;font-weight:400;font-variant-numeric:tabular-nums;letter-spacing:-.7px}.archive-counts span{font-size:12px;color:var(--muted)}
  .sources{display:grid;gap:14px;margin-top:22px}.source{padding:18px;border:1px solid var(--line);border-radius:12px}
  .extraction{background:light-dark(#faf9ff, rgb(156 146 255 / 6%))}.alignment{font-size:11px}
  .state{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px;white-space:nowrap}.state i{width:5px;height:5px}
  .bar{height:4px;border-radius:999px;background:light-dark(#ececed, #2e2e35);overflow:hidden;margin-top:16px}.bar span{display:block;height:100%;background:var(--accent);transition:width .4s}.bar.discovering span{width:24%;opacity:.45}
  .book-count{font-size:12px;margin:10px 0 0;color:var(--muted)}.book-count strong{color:var(--ink);font-weight:500}
  .stages{margin:16px 0 0;display:grid;gap:6px}.stages div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;font-size:12px}.stages dt{color:var(--muted)}.stages dd{margin:0;font-variant-numeric:tabular-nums}
  .current{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:14px 0 0}.progress-note{margin:8px 0 0;font-size:12px;color:var(--muted)}.paused{color:light-dark(#8a5a12, #e3b967)}
  .source-foot{display:flex;justify-content:space-between;gap:12px;margin-top:14px;font-size:10px;color:var(--muted)}
  .additions{list-style:none;padding:0;margin:16px 0 0}.additions li{display:flex;justify-content:space-between;gap:12px;font-size:12px}.additions small{color:var(--muted);font-size:11px}
  .progress-error{font-size:12px;color:light-dark(#a3341f, #ffab94)}.progress-error button{font:inherit;text-decoration:underline;background:none;border:0;color:inherit;cursor:pointer}
  @media(max-width:480px){.progress-dialog{padding:22px 18px}.source{padding:14px}.archive-counts strong{font-size:24px}}
</style>
