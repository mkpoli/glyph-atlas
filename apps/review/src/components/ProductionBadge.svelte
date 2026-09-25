<script module>
  import { t } from '../lib/i18n.svelte.js'
  const keys = { handwritten: 'handwritten', manuscript: 'handwritten', woodblock: 'woodblock',
    movable_type: 'movableType', 'movable-type': 'movableType', printed: 'printed', mixed: 'mixed' }
  export function productionKind(item) {
    const production = item?.production ?? item?.source?.production ?? 'unknown'
    return typeof production === 'string' ? production : production?.kind ?? production?.type ?? 'unknown'
  }
  /** How the record's page was made, or null when nobody has classified it. */
  export function productionLabel(item) {
    const key = keys[productionKind(item)]
    return key ? t(`production.${key}`) : null
  }
</script>

<script>
  let { item = null } = $props()
  const kind = $derived(productionKind(item))
  const label = $derived(productionLabel(item) ?? t('production.notClassified'))
</script>

<span class="production-badge" data-production={kind} aria-label={t('production.aria', { label })}>{label}</span>

<style>
  .production-badge{display:inline-block;font-size:10px;line-height:1.5;font-weight:400;white-space:nowrap;color:var(--muted);letter-spacing:.01em}
</style>
