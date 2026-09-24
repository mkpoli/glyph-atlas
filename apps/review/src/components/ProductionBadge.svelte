<script>
  let { item = null } = $props()
  const labels = { handwritten: 'Handwritten', manuscript: 'Handwritten', woodblock: 'Woodblock',
    movable_type: 'Movable type', 'movable-type': 'Movable type', printed: 'Printed', mixed: 'Mixed', unknown: 'Not classified' }
  const production = $derived(item?.production ?? item?.source?.production ?? 'unknown')
  const kind = $derived(typeof production === 'string' ? production : production?.kind ?? production?.type ?? 'unknown')
  const label = $derived(labels[kind] ?? 'Not classified')
</script>

<span class="production-badge" data-production={kind} aria-label={`Production: ${label}`}>{label}</span>

<style>
  .production-badge{display:inline-block;font-size:10px;line-height:1.5;font-weight:400;white-space:nowrap;color:var(--muted);letter-spacing:.01em}
</style>
