<script>
  // The citation of what the page shows, one format at a time: text in the reader's language, BibTeX,
  // CSL-JSON and Hayagriva, with a copy button and, for the formats a program reads, the address that
  // serves it. The access date is the day the reader opens it, so nothing is dated before then.
  import { tick } from 'svelte'
  import { page } from '$app/state'
  import { t } from '../lib/i18n.svelte.js'
  import { citeText, today } from '../lib/citation.js'
  import { bibtex, citeAddress, csl, hayagriva } from '$worker/citation.ts'
  let { entry, class: className = 'copy-id' } = $props()
  let dialog, opened = $state(null), copied = $state(''), timer, shown = $state('text')
  const id = $props.id()
  const text = $derived(opened ? citeText(entry, page.url.origin, opened) : null)
  const formats = $derived(opened ? [
    { key: 'text', label: t('cite.text'), value: text.credit ? `${text.citation}\n${text.credit}` : text.citation },
    { key: 'bibtex', label: 'BibTeX', value: bibtex(entry, page.url.origin, today(opened)), address: citeAddress(entry, 'bibtex') },
    { key: 'csl', label: 'CSL-JSON', value: JSON.stringify([csl(entry, page.url.origin, today(opened))], null, 2), address: citeAddress(entry, 'csl') },
    { key: 'hayagriva', label: 'Hayagriva', value: hayagriva(entry, page.url.origin, today(opened)), address: citeAddress(entry, 'hayagriva') },
  ] : [])
  const format = $derived(formats.find(item => item.key === shown) ?? formats[0])
  // The content is drawn before the dialog opens, so its focus and its name are there at once.
  async function open(event) {
    event.stopPropagation()
    opened = new Date(); copied = ''; shown = 'text'
    await tick()
    dialog.showModal()
  }
  // Only a click on the backdrop closes it; a selection dragged out of the text is not one.
  function outside(event) {
    if (event.target === dialog && !getSelection()?.toString()) dialog.close()
  }
  async function copy() {
    try { await navigator.clipboard.writeText(format.value) } catch { return }
    copied = format.key; clearTimeout(timer); timer = setTimeout(() => copied = '', 1500)
  }
  // The tabs answer the arrow keys, as a tab list does.
  function step(event, index) {
    const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : null
    if (next === null) return
    event.preventDefault()
    const target = formats[(next + formats.length) % formats.length]
    shown = target.key; copied = ''
    dialog.querySelector(`[id="${id}-tab-${target.key}"]`)?.focus()
  }
</script>

<button type="button" class={className} data-cite onclick={open}>{t('cite.open')}</button>
<dialog class="cite-dialog" bind:this={dialog} onclose={() => opened = null} onclick={outside} aria-labelledby="{id}-title">
  {#if opened}<div class="cite-body">
    <header><h2 id="{id}-title">{t(`cite.heading.${entry.kind}`)}</h2><button type="button" class="icon-button" aria-label={t('cite.close')} onclick={() => dialog.close()}>×</button></header>
    <div class="cite-tabs" role="tablist" aria-labelledby="{id}-title">
      {#each formats as item, index (item.key)}<button type="button" role="tab" id="{id}-tab-{item.key}" aria-selected={item.key === format.key} aria-controls="{id}-panel"
        tabindex={item.key === format.key ? 0 : -1} onclick={() => { shown = item.key; copied = '' }} onkeydown={event => step(event, index)}>{item.label}</button>{/each}
    </div>
    <div class="cite-panel" role="tabpanel" id="{id}-panel" aria-labelledby="{id}-tab-{format.key}">
      {#if format.key === 'text'}<p class="cite-text">{text.citation}</p>{#if text.credit}<p class="cite-credit">{text.credit}</p>{/if}
      {:else}<pre>{format.value}</pre>{/if}
    </div>
    <div class="cite-actions">
      {#if format.address}<a class="cite-address" href={format.address} target="_blank" rel="noopener">{t('cite.address', { format: format.label })}</a>{/if}
      <button type="button" class="cite-copy" onclick={copy}>{copied === format.key ? t('cite.copied') : t('cite.copy')}</button>
    </div>
  </div>{/if}
</dialog>

<style>
  .cite-dialog{width:min(560px,calc(100vw - 32px));max-height:calc(100dvh - 32px);margin:auto;padding:0;border:1px solid var(--line);border-radius:12px;color:var(--ink);background:var(--surface);box-shadow:0 24px 80px var(--shadow);text-align:left;font-size:13px}
  .cite-body{padding:18px 20px}
  .cite-dialog::backdrop{background:var(--backdrop);backdrop-filter:blur(3px)}
  header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
  h2{font-size:15px;font-weight:600}
  header .icon-button{font-size:24px;color:var(--muted);padding:2px 6px}
  .cite-tabs{display:flex;gap:2px;overflow-x:auto;border-bottom:1px solid var(--line)}
  .cite-tabs button{flex-shrink:0;border:0;border-bottom:2px solid transparent;border-radius:6px 6px 0 0;background:transparent;padding:7px 10px;margin-bottom:-1px;font-size:12px;color:var(--muted)}
  .cite-tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
  .cite-panel{padding:14px 0 12px}
  .cite-text{font-size:14px;line-height:1.7;overflow-wrap:anywhere;user-select:text}
  .cite-credit{margin-top:8px;font-size:12px;line-height:1.6;color:var(--muted);overflow-wrap:anywhere;user-select:text}
  pre{margin:0;max-height:220px;overflow:auto;padding:10px 12px;border-radius:7px;background:var(--surface-sunken);font:11.5px/1.55 ui-monospace,SFMono-Regular,Consolas,"GenZui Sans",monospace;white-space:pre;user-select:text}
  .cite-actions{display:flex;align-items:center;justify-content:flex-end;gap:12px}
  .cite-address{margin-right:auto;font-size:12px;color:var(--accent)}
  .cite-address:hover{text-decoration:underline;text-underline-offset:3px}
  .cite-copy{padding:6px 14px;font-size:12px}
</style>
