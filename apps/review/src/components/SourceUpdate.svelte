<script>
  import api from '../lib/api.js'
  import { sourcePatch } from '../lib/source-patch.js'
  let { pageId, enabled = true } = $props()
  let result = $state(null)
  let busy = $state(false)
  let error = $state('')

  async function prepare() {
    busy = true; error = ''; result = null
    try { result = await api.sourceUpdates([pageId]) }
    catch (e) { error = e.message }
    finally { busy = false }
  }
  function download(content, name, type) {
    const url = URL.createObjectURL(new Blob([content], { type }))
    const link = document.createElement('a'); link.href = url; link.download = name; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
</script>

<section class="source-update">
  <div class="section-heading compact"><div><p class="eyebrow">Return to ainu-records</p><h3>Reviewable source update</h3></div><button onclick={prepare} disabled={busy || !enabled}>{busy ? 'Checking source…' : 'Prepare source update'}</button></div>
  <p class="small muted">Checks the saved corrections against the connected source. Downloaded changes can be reviewed before applying them.</p>
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}
  {#if result}
    {#if result.validated}
      <p class="notice success" role="status">Source validation passed. {result.files.length} file{result.files.length === 1 ? '' : 's'} ready for review.</p>
      {#each result.files as file}<details class="source-file" open><summary>{file.path}</summary><div class="file-comparison"><div><h4>Current source</h4><pre>{file.before ?? '(New file)'}</pre></div><div><h4>Proposed update</h4><pre>{file.after}</pre></div></div></details>{/each}
      <div class="row"><button class="primary" onclick={() => download(sourcePatch(result.files), 'ainu-records-corrections.patch', 'text/x-diff')}>Download patch</button><button onclick={() => download(JSON.stringify(result, null, 2) + '\n', 'ainu-records-source-update.json', 'application/json')}>Download review record</button></div>
      <p class="small muted">Validation checks placement and format. Review the reading against the scan before applying the patch. No changes have been published.</p>
    {:else if result.unverified}<p class="notice" role="status">Source validation is unavailable. {result.reason} Your saved corrections remain in this workspace.</p>
    {:else}<p class="notice error" role="alert">The source update needs attention.</p><ul class="conflict-list">{#each result.conflicts ?? [] as conflict}<li>{conflict.id ? `${conflict.id}: ` : ''}{conflict.reason}</li>{/each}</ul>{/if}
  {/if}
</section>
