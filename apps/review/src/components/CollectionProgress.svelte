<script>
  import { onMount } from 'svelte'
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
  const phases = { discovering: 'Finding works', collecting: 'Collecting', publishing: 'Updating search', complete: 'Collected' }
  const number = value => Number(value ?? 0).toLocaleString()
  const clock = value => value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  const percent = source => source.total ? Math.min(100, Math.round(source.completed / source.total * 100)) : 0

  async function read() {
    try {
      const response = await fetch('/atlas/collection/status', {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        redirect: 'error', headers: { accept: 'application/json' },
      })
      if (!response.ok) throw new Error(`Could not read progress (${response.status}).`)
      data = await response.json(); error = ''
    } catch (e) {
      if (e.name !== 'AbortError') error = e.name === 'TimeoutError' ? 'Progress check timed out. Showing the last update.' : e.message
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
    <h2 id="progress-title">Collection</h2>
    <button class="icon-button" aria-label="Close collection progress" onclick={close}>×</button>
  </div>
  {#if error}<p class="progress-error" role="alert">{error} <button onclick={read}>Try again</button></p>{/if}
  {#if loading && !data}<p class="progress-note" role="status">Loading progress…</p>{/if}
  {#if data}
    {#if data.archive}
      <div class="archive-counts">
        <p><strong>{number(data.archive.character_crops)}</strong><span>character crops</span></p>
        <p><strong>{number(data.archive.works_with_crops)}</strong><span>works with crops</span></p>
      </div>
      <p class="progress-note">{number(data.archive.text_works)} works with searchable text</p>
    {/if}
    <div class="sources">
      {#if data.extraction}
        <section class="source extraction" aria-label="New character extraction progress">
          <div class="source-heading"><h3>New character crops</h3><span class="state">{data.extraction.state === 'running' ? 'Extracting' : data.extraction.state?.startsWith('paused') ? 'Paused' : 'Waiting'}</span></div>
          <dl class="stages">
            <div><dt>Machine crops published</dt><dd>{number(data.extraction.published_crops)}</dd></div>
            <div><dt>Works with new crops</dt><dd>{number(data.extraction.books_with_crops)}</dd></div>
            <div><dt>Pages extracted</dt><dd>{number(data.extraction.counts?.complete)}</dd></div>
            <div><dt>Located pages queued</dt><dd>{number((data.extraction.counts?.pending ?? 0) + (data.extraction.counts?.running ?? 0) + (data.extraction.counts?.retry ?? 0))}</dd></div>
          </dl>
          {#if data.extraction.counts?.failed}<p class="progress-note">{number(data.extraction.counts.failed)} pages need another pass</p>{/if}
          <p class="progress-note">From transcriptions with image positions</p>
        </section>
      {/if}
      {#each data.sources ?? [data] as source}
        <section class="source" aria-label={`${source.name ?? 'Honkoku'} collection progress`}>
          <div class="source-heading">
            <h3>{source.name ?? 'Honkoku'}</h3>
            <span class:paused={source.status === 'paused'} class="state">
              {#if source.status === 'running'}<i class="live-dot"></i>{phases[source.phase] ?? source.phase}
              {:else if source.status === 'waiting'}Waiting
              {:else if source.status === 'paused'}Paused
              {:else if source.phase === 'complete'}Collected
              {:else}Stopped{/if}
            </span>
          </div>
          {#if source.discovery_complete}
            <div class="bar" role="img" aria-label={`${percent(source)}% of discovered works collected`}><span style={`width:${percent(source)}%`}></span></div>
            <p class="book-count"><strong>{number(source.completed)}</strong> / {number(source.total)} works collected</p>
          {:else}
            <div class="bar discovering" role="img" aria-label="Work discovery is still in progress"><span></span></div>
            <p class="book-count"><strong>{number(source.total)}</strong> works found · discovery continuing</p>
            {#if source.discovered_pages}<p class="progress-note">{number(source.discovered_pages)} pages found</p>{/if}
          {/if}
          <dl class="stages">
            <div><dt>Searchable works published</dt><dd>{number(source.published_books)}</dd></div>
            <div><dt>Text pages collected</dt><dd>{number(source.text_pages)}</dd></div>
            <div><dt>Character crops</dt><dd>{number(source.character_crops)}</dd></div>
          </dl>
          {#if source.import_geometry === 'page_text_only'}<p class="progress-note alignment">Text imports · image alignment still needed</p>{/if}
          {#if source.current}<p class="current" title={source.current.title}>{source.current.title ?? source.current.id}</p>{/if}
          {#if source.status === 'paused' && source.pause_reason}<p class="progress-note paused">{source.pause_reason}</p>
          {:else if source.next_at}<p class="progress-note">Next work at {clock(source.next_at)}</p>{/if}
          <div class="source-foot">
            <span>{source.failed ? `${number(source.failed)} unavailable` : `${source.pause_seconds ?? 60}s between works`}</span>
            <span>{source.updated_at ? `Updated ${clock(source.updated_at)}` : ''}</span>
          </div>
        </section>
      {/each}
    </div>
    {#if data.additions?.length}
      <ul class="additions">
        {#each data.additions.slice(0, 3) as addition}<li><span>{addition.title}</span><small>{number(addition.pages)} scan references</small></li>{/each}
      </ul>
    {/if}
  {/if}
</dialog>

<style>
  .progress-dialog{width:min(500px,calc(100vw - 32px));max-height:calc(100dvh - 32px);margin:auto;padding:26px 28px;border:1px solid var(--line);border-radius:16px;color:var(--ink);background:#fff;box-shadow:0 24px 80px #0002;overflow:auto}
  .progress-dialog::backdrop{background:#17171b66;backdrop-filter:blur(3px)}
  .progress-heading,.source-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
  h2{font-size:22px;font-weight:500;letter-spacing:-.4px;margin:0}h3{font-size:15px;font-weight:500;margin:0}
  .archive-counts{display:grid;grid-template-columns:1.2fr 1fr;gap:20px;margin-top:22px}
  .archive-counts p{margin:0;display:flex;flex-direction:column;gap:4px}.archive-counts strong{font-size:28px;font-weight:400;font-variant-numeric:tabular-nums;letter-spacing:-.7px}.archive-counts span{font-size:12px;color:var(--muted)}
  .sources{display:grid;gap:14px;margin-top:22px}.source{padding:18px;border:1px solid var(--line);border-radius:12px}
  .extraction{background:#faf9ff}.alignment{font-size:11px}
  .state{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px;white-space:nowrap}.state i{width:5px;height:5px}
  .bar{height:4px;border-radius:999px;background:#ececed;overflow:hidden;margin-top:16px}.bar span{display:block;height:100%;background:var(--accent,#6853e8);transition:width .4s}.bar.discovering span{width:24%;opacity:.45}
  .book-count{font-size:12px;margin:10px 0 0;color:var(--muted)}.book-count strong{color:var(--ink);font-weight:500}
  .stages{margin:16px 0 0;display:grid;gap:6px}.stages div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;font-size:12px}.stages dt{color:var(--muted)}.stages dd{margin:0;font-variant-numeric:tabular-nums}
  .current{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:14px 0 0}.progress-note{margin:8px 0 0;font-size:12px;color:var(--muted)}.paused{color:#8a5a12}
  .source-foot{display:flex;justify-content:space-between;gap:12px;margin-top:14px;font-size:10px;color:var(--muted)}
  .additions{list-style:none;padding:0;margin:16px 0 0}.additions li{display:flex;justify-content:space-between;gap:12px;font-size:12px}.additions small{color:var(--muted);font-size:11px}
  .progress-error{font-size:12px;color:#a3341f}.progress-error button{font:inherit;text-decoration:underline;background:none;border:0;color:inherit;cursor:pointer}
  @media(max-width:480px){.progress-dialog{padding:22px 18px}.source{padding:14px}.archive-counts strong{font-size:24px}}
</style>
