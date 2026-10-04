<script>
  // The citation of what the page shows, in the reader's language, as BibTeX and as CSL-JSON, each
  // with its own copy button, and the address that serves the CSL-JSON. The access date is the day the
  // reader opens it, so nothing is dated before then.
  import { tick } from 'svelte'
  import { page } from '$app/state'
  import { t } from '../lib/i18n.svelte.js'
  import { citeText, today } from '../lib/citation.js'
  import { bibtex, citeAddress, csl } from '$worker/citation.ts'
  let { entry, class: className = 'copy-id' } = $props()
  let dialog, opened = $state(null), copied = $state(''), timer
  const heading = $props.id()
  const formats = $derived(opened ? [
    { key: 'text', label: t('cite.text'), value: citeText(entry, page.url.origin, opened) },
    { key: 'bibtex', label: 'BibTeX', value: bibtex(entry, page.url.origin, today(opened)) },
    { key: 'csl', label: 'CSL-JSON', value: JSON.stringify([csl(entry, page.url.origin, today(opened))], null, 2) },
  ] : [])
  // The content is drawn before the dialog opens, so its focus and its name are there at once.
  async function open(event) {
    event.stopPropagation()
    opened = new Date(); copied = ''
    await tick()
    dialog.showModal()
  }
  // Only a click on the backdrop closes it; a selection dragged out of the text is not one.
  function outside(event) {
    if (event.target === dialog && !getSelection()?.toString()) dialog.close()
  }
  async function copy(format) {
    try { await navigator.clipboard.writeText(format.value) } catch { return }
    copied = format.key; clearTimeout(timer); timer = setTimeout(() => copied = '', 1500)
  }
</script>

<button type="button" class={className} data-cite onclick={open}>{t('cite.open')}</button>
<dialog class="cite-dialog" bind:this={dialog} onclose={() => opened = null} onclick={outside} aria-labelledby={heading}>
  {#if opened}<div class="cite-body">
    <header><h2 id={heading}>{t(`cite.heading.${entry.kind}`)}</h2><button type="button" class="icon-button" aria-label={t('cite.close')} onclick={() => dialog.close()}>×</button></header>
    {#each formats as format (format.key)}
      <section class="cite-format">
        <div class="cite-format-head"><h3>{format.label}</h3><button type="button" class="cite-copy" onclick={() => copy(format)}>{copied === format.key ? t('cite.copied') : t('cite.copy')}</button></div>
        {#if format.key === 'text'}<p class="cite-text">{format.value}</p>{:else}<pre>{format.value}</pre>{/if}
      </section>
    {/each}
    <a class="cite-address" href={citeAddress(entry)} target="_blank" rel="noopener">{t('cite.address')}</a>
  </div>{/if}
</dialog>

<style>
  .cite-dialog{width:min(560px,calc(100vw - 32px));max-height:calc(100dvh - 32px);margin:auto;padding:0;border:1px solid var(--line);border-radius:12px;color:var(--ink);background:var(--surface);box-shadow:0 24px 80px var(--shadow);text-align:left}
  .cite-body{padding:20px 22px 22px}
  .cite-dialog::backdrop{background:var(--backdrop);backdrop-filter:blur(3px)}
  header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}
  h2{font-size:15px;font-weight:600}
  header .icon-button{font-size:24px;color:var(--muted);padding:2px 6px}
  .cite-format{padding:12px 0;border-top:1px solid var(--line)}
  .cite-format:first-of-type{border-top:0}
  .cite-format-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}
  h3{margin:0;font-size:11px;font-weight:500;color:var(--muted)}
  .cite-copy{padding:5px 12px;font-size:12px}
  .cite-text{font-size:13px;line-height:1.6;overflow-wrap:anywhere;user-select:text}
  pre{margin:0;max-height:180px;overflow:auto;padding:10px 12px;border-radius:7px;background:var(--surface-sunken);font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,"GenZui Sans",monospace;white-space:pre-wrap;overflow-wrap:anywhere;user-select:text}
  .cite-address{display:inline-block;margin-top:6px;font-size:12px;color:var(--accent)}
  .cite-address:hover{text-decoration:underline;text-underline-offset:3px}
</style>
