<script>
  // A quiet note in the corner while the site cannot take everything at once, which an import of the
  // database makes it for a minute or two: reads are being tried again, and saves are kept on this
  // device until they are sent. It is a popover so it shows above an open inspector, which is a modal
  // dialog.
  import { onMount } from 'svelte'
  import { page } from '$app/state'
  import { invalidateAll } from '$app/navigation'
  import { busy, backoff, pause } from '../lib/busy.svelte.js'
  import { outbox, resume, dismiss } from '../lib/outbox.svelte.js'
  import { t } from '../lib/i18n.svelte.js'

  let note
  // A page whose own load found the database busy is loaded again until it answers.
  const stalled = $derived(page.error?.code === 'busy')
  const reading = $derived(busy.reads > 0 || stalled)
  const shown = $derived(reading || outbox.pending > 0 || outbox.refused.length > 0)
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
    void busy.reads, outbox.pending, outbox.refused.length
    if (!note?.showPopover) return
    if (note.matches(':popover-open')) note.hidePopover()
    if (shown) note.showPopover()
  })
  // Saves an earlier page kept are sent once this one is up.
  onMount(resume)
</script>

<div class="database-status" popover="manual" role="status" bind:this={note}>
  {#if outbox.pending}<p><span class="spinner" aria-hidden="true"></span>{t('client.keptSending', { count: outbox.pending })}</p>
  {:else if reading}<p><span class="spinner" aria-hidden="true"></span>{t('client.busyRetrying')}</p>{/if}
  {#each outbox.refused as refusal (refusal.key)}
    <p class="refused">{t('client.keptRefused', { reason: refusal.message })}
      <button type="button" onclick={() => dismiss(refusal.key)}>{t('client.keptDismiss')}</button></p>
  {/each}
</div>

<style>
  .database-status{inset:auto auto 16px 16px;margin:0;max-width:min(420px,calc(100vw - 32px));box-sizing:border-box;flex-direction:column;gap:6px;
    background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:18px;padding:8px 14px;font-size:12px;box-shadow:0 5px 20px var(--shadow)}
  .database-status:popover-open{display:flex}
  p{margin:0;display:flex;align-items:center;gap:8px}
  .refused{color:var(--wrong);flex-wrap:wrap}
  .refused button{font:inherit;color:var(--ink);background:none;border:1px solid var(--line);border-radius:6px;padding:2px 8px;cursor:pointer}
  .refused button:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  .spinner{width:10px;height:10px;flex:none;border:2px solid var(--line);border-top-color:var(--muted);border-radius:50%;animation:turn 1s linear infinite}
  @keyframes turn{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion:reduce){.spinner{animation:none}}
</style>
