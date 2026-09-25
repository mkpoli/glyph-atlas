<script module>
  import { LOCALES, t } from '../lib/i18n.svelte.js'
  // A production is a node of `data/vocab/production.yaml`, written as its path; each node has a
  // catalogue key `production.kind.<path with underscores>`.
  const segment = production => production.replaceAll('/', '_')
  const key = production => `production.kind.${segment(production)}`
  const known = new Set(Object.keys(LOCALES.find(locale => locale.tag === 'en').messages))
  export function productionKind(item) {
    const production = item?.production ?? item?.source?.production
    return typeof production === 'string' && known.has(key(production)) ? production : 'unknown'
  }
  /** How the record's page was made, or null when nobody has classified it. */
  export function productionLabel(item) {
    const kind = productionKind(item)
    return kind === 'unknown' ? null : t(`production.kind.${segment(kind)}`)
  }
</script>

<script>
  let { item = null } = $props()
  const kind = $derived(productionKind(item))
  const label = $derived(productionLabel(item) ?? t('production.kind.unknown'))
</script>

<span class="production-badge" data-production={kind} aria-label={t('production.aria', { label })}>{label}</span>

<style>
  .production-badge{display:inline-block;font-size:10px;line-height:1.5;font-weight:400;white-space:nowrap;color:var(--muted);letter-spacing:.01em}
</style>
