<script module>
  const labels = { handwritten: 'Handwritten', manuscript: 'Handwritten', woodblock: 'Woodblock',
    movable_type: 'Movable type', 'movable-type': 'Movable type', printed: 'Printed', mixed: 'Mixed' }
  export function productionKind(item) {
    const production = item?.production ?? item?.source?.production ?? 'unknown'
    return typeof production === 'string' ? production : production?.kind ?? production?.type ?? 'unknown'
  }
  /** How the record's page was made, or null when nobody has classified it. */
  export function productionLabel(item) {
    return labels[productionKind(item)] ?? null
  }
</script>

<script>
  let { item = null } = $props()
  const kind = $derived(productionKind(item))
  const label = $derived(productionLabel(item) ?? 'Not classified')
</script>

<span class="production-badge" data-production={kind} aria-label={`Production: ${label}`}>{label}</span>

<style>
  .production-badge{display:inline-block;font-size:10px;line-height:1.5;font-weight:400;white-space:nowrap;color:var(--muted);letter-spacing:.01em}
</style>
