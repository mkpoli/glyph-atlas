<script>
  // Which work the grid shows: every work, or one. The list is the catalogue's own count of crops per
  // work, so a work with nothing to show is not offered. `count` is the number each row shows. A chosen
  // work keeps its name after a refresh drops it from the list, so the toggle never claims all works
  // while one is still applied.
  import { t } from '../lib/i18n.svelte.js'
  import { number } from '../lib/client.js'

  let { works = [], value = '', onchange = () => {} } = $props()
  let open = $state(false), find = $state(''), root = $state(null), field = $state(null), toggleButton = $state(null)
  let named = $state(null)
  const chosen = $derived(works.find(work => work.id === value) ?? (value ? (named?.id === value ? named : { id: value, title: null }) : null))
  const shown = $derived(works.filter(work => (work.title ?? work.id).toLowerCase().includes(find.trim().toLowerCase())))

  function choose(work) {
    named = work
    open = false
    find = ''
    toggleButton?.focus()
    onchange(work?.id ?? '')
  }

  function left(event) {
    if (open && !root?.contains(event.relatedTarget)) open = false
  }

  function toggle() {
    open = !open
    if (open) queueMicrotask(() => field?.focus())
  }

  function outside(event) {
    if (open && !root?.contains(event.target)) open = false
  }
</script>

<svelte:window onpointerdown={outside} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="work-control" bind:this={root} onfocusout={left} onkeydown={e => { if (e.key === 'Escape' && open) { e.preventDefault(); open = false; toggleButton?.focus() } }}>
  <button class="work-toggle" bind:this={toggleButton} aria-expanded={open} aria-label={t('explore.works.label')} onclick={toggle}>
    <span class="work-name" lang={chosen ? 'ja' : undefined}>{chosen ? (chosen.title ?? chosen.id) : t('explore.works.all')}</span><span aria-hidden="true">⌄</span>
  </button>
  {#if open}
    <div class="work-menu" role="dialog" aria-label={t('explore.works.label')}>
      <input bind:this={field} bind:value={find} aria-label={t('explore.works.find')} placeholder={t('explore.works.find')} />
      <ul>
        <li><button class:chosen={!value} onclick={() => choose(null)}>{t('explore.works.all')}</button></li>
        {#each shown as work (work.id)}
          <li><button class:chosen={work.id === value} onclick={() => choose(work)} title={work.title ?? work.id}>
            <span lang="ja">{work.title ?? work.id}</span><small>{number(work.count ?? work.total)}</small>
          </button></li>
        {:else}
          <li class="work-none">{t('explore.works.none')}</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .work-control{position:relative;min-width:0;padding-left:25px;border-left:1px solid var(--line)}
  .work-toggle{display:flex;align-items:center;gap:14px;max-width:260px;border:0;background:transparent;padding:8px 0;font-size:12px}
  .work-toggle>span:last-child{color:var(--muted)}
  .work-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .work-menu{position:absolute;top:42px;left:0;z-index:8;width:360px;max-width:calc(100vw - 32px);padding:12px;border:1px solid var(--line);border-radius:9px;background:#fff;box-shadow:0 14px 40px #0001}
  .work-menu input{width:100%;font-size:13px;padding:9px 10px}
  .work-menu ul{list-style:none;margin:8px 0 0;padding:0;max-height:340px;overflow:auto}
  .work-menu button{display:flex;align-items:baseline;gap:12px;width:100%;border:0;border-radius:5px;background:transparent;padding:8px 10px;text-align:left;font-size:13px}
  .work-menu button:hover,.work-menu button.chosen{background:var(--accent-light);color:var(--accent)}
  .work-menu button span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .work-menu small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .work-none{padding:10px;font-size:12px;color:var(--muted)}
  @media(max-width:700px){.work-control{padding-left:12px}.work-toggle{max-width:160px;font-size:11px}.work-menu{left:auto;right:0;width:300px}}
</style>
