<script>
  // A quiet note in the corner while the database is busy, which an import makes it for a minute or
  // two: reads are being tried again, and the page keeps what it shows meanwhile. It is a popover so
  // it shows above an open inspector, which is a modal dialog.
  import { page } from '$app/state'
  import { invalidateAll } from '$app/navigation'
  import { busy, backoff, pause } from '../lib/busy.svelte.js'
  import { t } from '../lib/i18n.svelte.js'

  let note
  // A page whose own load found the database busy is loaded again until it answers.
  const stalled = $derived(page.error?.code === 'busy')
  const waiting = $derived(busy.reads > 0 || stalled)
  $effect(() => {
    if (!stalled) return
    let stopped = false
    ;(async () => {
      for (let n = 0; !stopped && page.error?.code === 'busy'; n++) {
        await pause(backoff(n, 5))
        if (!stopped) await invalidateAll()
      }
    })()
    return () => { stopped = true }
  })
  // Shown again on each change, so it rises above a dialog opened since.
  $effect(() => {
    void busy.reads
    if (!note?.showPopover) return
    if (note.matches(':popover-open')) note.hidePopover()
    if (waiting) note.showPopover()
  })
</script>

<div class="database-status" popover="manual" role="status" bind:this={note}>
  {#if waiting}<span class="spinner" aria-hidden="true"></span>{t('client.busyRetrying')}{/if}
</div>

<style>
  .database-status{inset:auto auto 16px 16px;margin:0;max-width:calc(100vw - 32px);box-sizing:border-box;display:flex;align-items:center;gap:8px;
    background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:50px;padding:8px 14px;font-size:12px;box-shadow:0 5px 20px var(--shadow)}
  .database-status:not(:popover-open){display:none}
  .spinner{width:10px;height:10px;flex:none;border:2px solid var(--line);border-top-color:var(--muted);border-radius:50%;animation:turn 1s linear infinite}
  @keyframes turn{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion:reduce){.spinner{animation:none}}
</style>
