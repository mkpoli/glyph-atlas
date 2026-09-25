<script>
  import { t } from '../lib/i18n.svelte.js'
  let { item, onload = () => {}, onerror = () => {}, eager = false } = $props()
  let failed = $state(false)
</script>
{#if failed}<span class="missing-glyph" aria-label={t('character.image.unavailable')}>—</span>
{:else}<img class="glyph-image" src={item.image} alt={t('character.glyph.alt', { label: item.label })} loading={eager ? 'eager' : 'lazy'} fetchpriority={eager ? 'high' : 'auto'} decoding="async" onload={() => onload(item.id)} onerror={() => { failed = true; onerror(item.id) }} />{/if}
