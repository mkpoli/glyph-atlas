<script>
  import { onMount } from 'svelte'
  import api from '../lib/api.js'

  let { session, documentId = '' } = $props()
  let pages = $state([])
  let loading = $state(true)
  let error = $state('')
  let search = $state('')
  let filter = $state('all')
  let position = $state(0)
  let closed = false
  const size = 18
  const titles = $derived(new Map(session.documents.map(d => [d.id, d])))
  const selected = $derived(titles.get(documentId))
  const pageState = (page) => {
    if (!page.transcribed) return 'transcription'
    if (!page.counts.total) return 'boxes'
    if (page.counts.unresolved) return 'unresolved'
    if (page.counts.draft) return 'draft'
    if (page.counts.checked && page.counts.checked + page.counts.retired >= page.counts.total) return 'reviewed'
    return 'machine'
  }
  const labels = { transcription: 'Needs transcription', boxes: 'Needs character boxes', unresolved: 'Unresolved readings', draft: 'Review in progress', reviewed: 'Characters reviewed', machine: 'Awaiting review' }
  const filtered = $derived(pages.filter(page => (filter === 'all' || pageState(page) === filter) &&
    `${titles.get(page.document_id)?.title ?? ''} ${page.seq + 1}`.toLowerCase().includes(search.toLowerCase())))
  const visible = $derived(filtered.slice(position, position + size))
  async function load() {
    loading = true; error = ''; pages = []
    try {
      let offset = 0
      do {
        const response = await api.pages({ document: documentId, limit: 2000, offset })
        if (closed) return
        pages = [...pages, ...response.items]
        offset += response.items.length
        if (offset >= response.total || !response.items.length) break
      } while (true)
    } catch (e) { error = e.message }
    finally { loading = false }
  }
  onMount(() => { load(); return () => { closed = true } })
</script>

<div class="workspace">
  <div class="section-heading"><div><p class="eyebrow">Source catalogue</p><h1>{selected?.title || 'Browse the collection'}</h1><p class="lede">{selected?.holder || 'Every imported page, including pages without transcription or character boxes.'}</p></div><a class="text-link" href="#/project">Project overview →</a></div>
  <div class="catalogue-controls">
    <label>Volume<select value={documentId} onchange={(e) => session.go('pages', e.currentTarget.value || null)}><option value="">All imported volumes</option>{#each session.documents as d}<option value={d.id}>{d.title || d.id}</option>{/each}</select></label>
    <label>Work remaining<select bind:value={filter} onchange={() => position = 0}><option value="all">All pages</option>{#each Object.entries(labels) as [value, label]}<option {value}>{label}</option>{/each}</select></label>
    <label>Find a page<input type="search" bind:value={search} oninput={() => position = 0} placeholder="Title or page number" /></label>
  </div>
  {#if error}<p class="notice error" role="alert">{error} <button onclick={load}>Retry</button></p>{/if}
  <div class="section-heading compact"><p class="muted small" aria-live="polite">{loading ? 'Loading pages…' : `${filtered.length.toLocaleString()} pages`}</p><span class="small muted">Select a page to read and leave feedback</span></div>
  <div class="page-catalogue">
    {#each visible as page (page.id)}
      <a class="page-card" href="#/page/{encodeURIComponent(page.id)}">
        <div class="page-preview"><img src={page.image_url} alt="" loading="lazy" onerror={(e) => e.currentTarget.style.visibility = 'hidden'} /><span>p. {page.seq + 1}</span></div>
        <div class="page-card-content"><strong class="source-title">{titles.get(page.document_id)?.title || 'Source'}</strong><span class="page-number">Page {page.seq + 1}</span><span class="page-state" class:needs-attention={['unresolved', 'transcription'].includes(pageState(page))}>{labels[pageState(page)]}</span><small>{page.counts.checked.toLocaleString()} / {page.counts.total.toLocaleString()} characters reviewed</small></div>
      </a>
    {/each}
  </div>
  {#if !loading && !filtered.length}<p class="empty-state">No pages match these filters.</p>{/if}
  <div class="pagination"><button disabled={!position} onclick={() => position = Math.max(0, position - size)}>← Previous</button><span>{filtered.length ? `${position + 1}–${Math.min(position + size, filtered.length)} of ${filtered.length}` : '0 pages'}</span><button disabled={position + size >= filtered.length} onclick={() => position += size}>Next →</button></div>
</div>
