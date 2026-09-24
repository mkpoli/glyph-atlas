<script>
  import { onMount } from 'svelte'
  let { close } = $props()
  let dialog, preview = $state(null)
  let text = $state(''), count = $state(0), error = $state(''), status = $state(''), ready = $state(false)
  let canSave = $state(false), saving = $state(false)
  let includeProcessed = $state(false), generation = 0
  const query = $derived(includeProcessed ? '?include_processed=true' : '')
  const controller = new AbortController()

  onMount(() => {
    dialog.showModal()
    canSave = typeof window.showSaveFilePicker === 'function'
    prepare()
    return () => controller.abort()
  })

  async function prepare() {
    const current = ++generation
    error = ''; status = ''; ready = false
    try {
      const response = await fetch('/atlas/reviews' + query, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        redirect: 'error', headers: { accept: 'application/json' },
      })
      if (!response.ok) throw new Error(`Could not export reviews (${response.status}).`)
      const data = await response.json()
      if (current !== generation || controller.signal.aborted) return
      if (!Array.isArray(data.reviews)) throw new Error('The export did not contain reviews.')
      text = JSON.stringify(data, null, 2) + '\n'
      count = data.reviews.length
      ready = true
    } catch (e) { if (current === generation && e.name !== 'AbortError') error = e.name === 'TimeoutError' ? 'The export timed out. Try again.' : e.message }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      status = 'Copied'
    } catch {
      preview.focus(); preview.select()
      status = 'Press Ctrl+C to copy the selected JSON.'
    }
  }

  async function saveAs() {
    if (saving) return
    saving = true; status = ''
    let writable
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: 'atlas-character-reviews.json',
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }],
      })
      writable = await handle.createWritable()
      await writable.write(text)
      await writable.close()
      status = `Saved ${handle.name}`
    } catch (e) {
      if (writable) await writable.abort().catch(() => {})
      status = e.name === 'AbortError' ? 'Save cancelled' : 'Could not save. Try Download JSON or Copy JSON.'
    } finally { saving = false }
  }
</script>

<dialog class="export-dialog" bind:this={dialog} oncancel={close} aria-labelledby="export-title">
  <div class="export-heading"><h2 id="export-title">Export reviews</h2><button class="icon-button" aria-label="Close export" onclick={close}>×</button></div>
  <label class="export-history"><input type="checkbox" checked={includeProcessed} onchange={e => { includeProcessed = e.currentTarget.checked; prepare() }} disabled={saving} />Include processed reviews</label>
  {#if error}<p role="alert">{error}</p><button onclick={prepare}>Try again</button>
  {:else if !ready}<p role="status">Preparing export…</p>
  {:else}
    <p class="export-count">{count ? `${count} ${includeProcessed ? 'saved' : 'new'} ${count === 1 ? 'review' : 'reviews'}` : 'No new reviews to export.'}</p>
    {#if count}
    <div class="export-actions">
      <a class="primary" href={'/atlas/reviews.json' + query} download="atlas-character-reviews.json">Download JSON ↓</a>
      {#if canSave}<button class="save-export" onclick={saveAs} disabled={saving}>{saving ? 'Saving…' : 'Save as…'}</button>{/if}
      <button class="copy-export" onclick={copy}>Copy JSON</button>
    </div>
    <textarea bind:this={preview} aria-label="Reviews JSON" readonly value={text} spellcheck="false"></textarea>
    <div class="export-foot"><span role="status">{status}</span><a href={'/atlas/reviews' + query} target="_blank" rel="noreferrer">Open JSON ↗</a></div>
    {/if}
  {/if}
</dialog>

<style>
  .export-dialog{width:min(620px,calc(100vw - 32px));max-height:calc(100dvh - 32px);margin:auto;padding:28px;border:1px solid var(--line);border-radius:12px;color:var(--ink);background:#fff;box-shadow:0 24px 80px #0002}
  .export-dialog::backdrop{background:#17171b66;backdrop-filter:blur(3px)}
  .export-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:12px}
  h2{font-size:24px;font-weight:500;letter-spacing:-.6px;margin:0}
  .export-count{color:var(--muted);font-size:13px}
  .export-history{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);margin:14px 0}
  .export-actions{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 16px}
  .export-actions a,.export-actions button{padding:12px 18px;border-radius:7px;font-size:13px}
  textarea{width:100%;height:230px;resize:vertical;font:11px/1.6 ui-monospace,monospace;white-space:pre;overflow:auto;background:#f7f7f8}
  .export-foot{display:flex;justify-content:space-between;gap:14px;margin-top:12px;font-size:11px;color:var(--muted)}
  .export-foot a{text-decoration:underline;white-space:nowrap}
  [role=alert]{margin-bottom:16px}
</style>
