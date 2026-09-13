<script>
  import { onMount } from 'svelte'
  import api from '../lib/api.js'

  let project = $state(null)
  let error = $state('')
  let refreshing = $state(false)
  const number = (n) => Number(n ?? 0).toLocaleString()
  async function refresh() {
    refreshing = true
    error = ''
    try { project = await api.project() } catch (e) { error = e.message }
    finally { refreshing = false }
  }
  onMount(() => { refresh() })
  const counts = $derived(project?.counts ?? {})
  const outstanding = $derived((counts.machine ?? 0) + (counts.unresolved ?? 0) + (counts.draft ?? 0))
  const progress = (c) => c.total ? Math.round(100 * c.checked / c.total) : 0
</script>

<div class="workspace overview">
  <div class="section-heading">
    <div><p class="eyebrow">Collection workspace</p><h1>Project overview</h1>
      <p class="lede">Read the scans, review the transcription, and prepare corrections for ainu-records.</p></div>
    <button onclick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh status'}</button>
  </div>
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}
  {#if project}
    <div class="metrics">
      <div><span>Pages in this workspace</span><strong>{number(project.imported.pages)}</strong><small>{number(project.imported.documents)} imported volumes</small></div>
      <div><span>Characters awaiting review</span><strong>{number(outstanding)}</strong><small>{number(counts.unresolved)} unresolved · {number(counts.draft)} drafts</small></div>
      <div><span>Editorial decisions recorded</span><strong>{number(counts.checked)}</strong><small>Current decisions, including imported reviews</small></div>
      <div><span>Audited accuracy</span><strong class="metric-word">{project.quality?.rate == null ? 'Unmeasured' : `${(100 * project.quality.rate).toFixed(1)}%`}</strong><small>{project.quality?.rate == null ? 'A scored audit is needed to measure quality.' : 'From the scored audit sample'}</small></div>
    </div>

    <div class="section-heading compact"><div><p class="eyebrow">Imported sources</p><h2>Volumes to review</h2></div><a class="text-link" href="#/pages">Browse every page →</a></div>
    <div class="source-table-wrap">
      <table class="source-table">
        <thead><tr><th>Source / physical copy</th><th>Pages</th><th>Characters</th><th>Review progress</th><th><span class="sr-only">Open</span></th></tr></thead>
        <tbody>{#each project.documents as document (document.id)}
          <tr>
            <td><a class="source-title" href="#/pages/{encodeURIComponent(document.id)}">{document.title || document.id}</a><div class="muted small">{document.holder || 'Collection not recorded'}{document.source?.part ? ` · ${document.source.part}` : ''}</div></td>
            <td>{number(document.pages)}</td><td>{number(document.counts.total)}</td>
            <td>{#if document.counts.total}<div class="review-meter" aria-label={`${number(document.counts.checked)} editorial decisions out of ${number(document.counts.total)} characters`}><span style:width={`${progress(document.counts)}%`}></span></div><div class="small muted">{number(document.counts.checked)} reviewed · {number(document.counts.unresolved)} unresolved</div>{:else}<span class="small muted">Needs character boxes</span>{/if}<div class="small muted">{number(document.boxed_pages)} of {number(document.pages)} pages mapped</div></td>
            <td><a href="#/pages/{encodeURIComponent(document.id)}" aria-label={`Browse ${document.title || 'source'}`}>Open →</a></td>
          </tr>
        {/each}</tbody>
      </table>
      {#if !project.documents.length}<p class="empty-state">No sources have been imported into this workspace.</p>{/if}
    </div>

    <div class="overview-notes">
      <section><p class="eyebrow">Reading the status</p><h2>Review work and measured quality</h2><p>A saved note or adjusted crop remains a draft. Machine results need review. Editorial decisions record what a reviewer chose; an audit measures whether those decisions are correct.</p><a class="text-link" href="#/queue">Open character review →</a></section>
      <section><p class="eyebrow">ainu-records</p><h2>Corrections that can return to the source</h2>
        {#if project.source?.readable}<p>The connected source has {number(project.source.works)} works and {number(project.source.corrections)} editorial corrections. This workspace contains the imported volumes listed above.</p>
        {:else}<p>Source validation is {project.source ? 'unavailable for the configured checkout' : 'not connected'}. Local feedback can still be saved.</p>{/if}
        <p>Prepare a source update from the page reader to check its target and review the proposed file changes.</p>
      </section>
    </div>
  {:else if !error}<p class="empty-state" role="status">Loading project status…</p>{/if}
</div>
