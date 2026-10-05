<script>
  // A crop id is quoted in issues and chat, so every place that shows one lets it be copied.
  // `inline` is the hover card on a review tile: the id itself is the control, and it stays out of
  // the tab order because the tile's own details are hidden from assistive technology. `children` are
  // further controls for the record, set after the copy button.
  import { t } from '../lib/i18n.svelte.js'
  let { id, inline = false, children = null } = $props()
  let copied = $state(false), timer
  async function copy(event) {
    event.stopPropagation()
    try { await navigator.clipboard.writeText(id) } catch { return }
    copied = true; clearTimeout(timer); timer = setTimeout(() => copied = false, 1500)
  }
</script>

{#if inline}<button type="button" class="tile-id copy-inline" tabindex="-1" title={t('copyId.copy')} onclick={copy}>{copied ? t('copyId.copied') : id}</button>
{:else}<div class="record-id"><code>{id}</code><button type="button" class="copy-id" onclick={copy}>{copied ? t('copyId.copied') : t('copyId.copy')}</button>{@render children?.()}</div>{/if}

<style>
  .copy-inline{display:block;width:100%;padding:0;border:0;border-radius:0;background:none;text-align:left;cursor:copy}
  .copy-inline:hover{color:var(--ink);text-decoration:underline}
</style>
