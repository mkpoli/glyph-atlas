<script>
  let { item, onload = () => {}, onerror = () => {}, eager = false } = $props()
  let failed = $state(false)
</script>
{#if failed}<span class="missing-glyph" aria-label="Image unavailable">—</span>
{:else}<img class="glyph-image" src={item.image} alt={`Handwritten ${item.label}`} loading={eager ? 'eager' : 'lazy'} decoding="async" onload={() => onload(item.id)} onerror={() => { failed = true; onerror(item.id) }} />{/if}
