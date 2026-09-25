<script>
  import { onMount } from 'svelte'
  import { t } from '../lib/i18n.svelte.js'
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
      if (!response.ok) throw new Error(t('export.readError', { status: response.status }))
      const data = await response.json()
      if (current !== generation || controller.signal.aborted) return
      if (!Array.isArray(data.reviews)) throw new Error(t('export.noReviews'))
      text = JSON.stringify(data, null, 2) + '\n'
      count = data.reviews.length
      ready = true
    } catch (e) { if (current === generation && e.name !== 'AbortError') error = e.name === 'TimeoutError' ? t('export.timedOut') : e.message }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      status = t('export.copied')
    } catch {
      preview.focus(); preview.select()
      status = t('export.copyManually')
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
      status = t('export.saved', { name: handle.name })
    } catch (e) {
      if (writable) await writable.abort().catch(() => {})
      status = e.name === 'AbortError' ? t('export.cancelled') : t('export.saveFailed')
    } finally { saving = false }
  }
</script>

<dialog class="export-dialog" bind:this={dialog} oncancel={close} aria-labelledby="export-title">
  <div class="export-heading"><h2 id="export-title">{t('export.title')}</h2><button class="icon-button" aria-label={t('export.close')} onclick={close}>×</button></div>
  <label class="export-history"><input type="checkbox" checked={includeProcessed} onchange={e => { includeProcessed = e.currentTarget.checked; prepare() }} disabled={saving} />{t('export.includeProcessed')}</label>
  {#if error}<p role="alert">{error}</p><button onclick={prepare}>{t('common.tryAgain')}</button>
  {:else if !ready}<p role="status">{t('export.preparing')}</p>
  {:else}
    <p class="export-count">{count ? t(includeProcessed ? 'export.count.saved' : 'export.count.new', { count }) : t('export.empty')}</p>
    {#if count}
    <div class="export-actions">
      <a class="primary" href={'/atlas/reviews.json' + query} download="atlas-character-reviews.json">{t('export.download')}</a>
      {#if canSave}<button class="save-export" onclick={saveAs} disabled={saving}>{saving ? t('common.saving') : t('export.saveAs')}</button>{/if}
      <button class="copy-export" onclick={copy}>{t('export.copyJson')}</button>
    </div>
    <textarea bind:this={preview} aria-label={t('export.jsonTextarea')} readonly value={text} spellcheck="false"></textarea>
    <div class="export-foot"><span role="status">{status}</span><a href={'/atlas/reviews' + query} target="_blank" rel="noreferrer">{t('export.openJson')}</a></div>
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
