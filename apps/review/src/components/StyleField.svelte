<script>
  // The style a crop is written in, as one button per style. The one it has itself is filled, the one it
  // takes from its page or document is dashed, and pressing one is held until the dialog saves. Pressing
  // the crop's own takes it back. `item` is the crop as the server reports it; a server that reports no
  // style gets nothing here.
  import { inheritedStyle, ownStyle, pressStyle, stylesOffered } from '../lib/cropStyle.js'
  import { t } from '../lib/i18n.svelte.js'
  let { item, chosen = null, onchoose, disabled = false } = $props()
  let root = $state(null)
  const styles = $derived(stylesOffered(item))
  const inherited = $derived(inheritedStyle(item))
  const shown = $derived(chosen ?? ownStyle(item))
  const label = style => style === inherited && style !== shown ? t('style.inherit', { style: t(`style.kind.${style}`) }) : t(`style.kind.${style}`)
  const press = style => onchoose(pressStyle(item, chosen, style))

  // Shift and a digit press the buttons in order. On a layout whose digits need Shift the key is a digit
  // of its own, which the form bar takes.
  function keydown(event) {
    if (disabled || event.defaultPrevented || event.isComposing || !event.shiftKey || event.metaKey || event.ctrlKey || event.altKey || /^\d$/.test(event.key)) return
    const style = styles[Number(/^Digit([1-9])$/.exec(event.code)?.[1]) - 1]
    if (!style || event.target.closest?.('input, textarea, select, [contenteditable="true"], .character-search, .ids-composer')) return
    const dialog = [...document.querySelectorAll('dialog[open]')].at(-1)
    if (dialog ? !dialog.contains(root) : root.closest('dialog')) return
    event.preventDefault()
    press(style)
  }
</script>

<svelte:window onkeydown={keydown} />

{#if item?.style && item.style_editable}
  <div class="style-field" role="group" aria-label={t('style.label')} bind:this={root}>
    <span class="style-label" aria-hidden="true">{t('style.label')}</span>
    <div class="style-options">
      {#each styles as style, i (style)}
        <button type="button" class="style-option" class:chosen={style === shown} class:held={style === chosen} class:inherited={style === inherited && style !== shown} {disabled}
          aria-pressed={style === shown} aria-label={label(style)} aria-keyshortcuts={`Shift+${i + 1}`} title={`${label(style)} (⇧${i + 1})`} onclick={() => press(style)}>{t(`style.kind.${style}`)}</button>
      {/each}
    </div>
  </div>
{/if}

<style>
  .style-field{display:flex;gap:12px;align-items:baseline;margin:0 0 16px;font-size:12px}
  .style-label{flex:none;color:var(--muted)}
  .style-options{display:flex;flex-wrap:wrap;gap:8px;min-width:0}
  .style-option{height:32px;padding:0 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);font:inherit;white-space:nowrap;cursor:pointer}
  .style-option:hover:not(:disabled){border-color:var(--accent)}
  .style-option.inherited{border-style:dashed;border-color:var(--line-strong);color:var(--accent)}
  .style-option.chosen{border-color:var(--accent);background:var(--accent-light);color:var(--accent);font-weight:600}
  .style-option.held{border-width:2px;padding:0 11px}
  .style-option:disabled{opacity:.5;cursor:default}
  .style-option:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  @media(max-width:700px){.style-option{height:36px}}
</style>
