<script>
  // The style a crop is written in, as one button per style. The one it has itself is filled, the one it
  // takes from its page or document is dashed, and pressing one is held until the dialog saves. Pressing
  // the crop's own takes it back. `item` is the crop as the server reports it; a server that reports no
  // style gets nothing here.
  import StyleOptions from './StyleOptions.svelte'
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
    <StyleOptions {styles} pressed={shown} dashed={inherited} held={chosen} {disabled} keys name={label} onpress={press} />
  </div>
{/if}

<style>
  .style-field{display:flex;gap:12px;align-items:baseline;margin:0 0 16px;font-size:12px}
  .style-label{flex:none;color:var(--muted)}
</style>
