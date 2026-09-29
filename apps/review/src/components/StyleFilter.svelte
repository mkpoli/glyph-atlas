<script>
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  import { STYLE_GROUPS, STYLES_IN } from '../lib/style.js'
  // `counts` holds the crops of each style group, as the gallery's two lists report them together.
  let { counts = null, value = '', onchange = () => {} } = $props()
  const total = $derived(counts ? STYLE_GROUPS.reduce((sum, group) => sum + (counts[group] ?? 0), 0) : null)
  const label = group => group === 'unassessed' ? t('style.kind.unassessed') : t(`styleFilter.${group}`)
  const members = group => STYLES_IN[group].map(kind => t(`style.kind.${kind}`)).join(' · ')
</script>

<nav class="style-filter" aria-label={t('styleFilter.label')}>
  <button class:active={!value} aria-pressed={!value} onclick={() => onchange('')}>
    <span>{t('styleFilter.all')}</span>{#if total != null}<small>{formatNumber(total)}</small>{/if}
  </button>
  {#each STYLE_GROUPS as group (group)}
    <button class:active={value === group} aria-pressed={value === group} data-style={group}
            title={STYLES_IN[group].length > 1 ? members(group) : undefined} onclick={() => onchange(group)}>
      <span>{label(group)}</span>{#if counts}<small>{formatNumber(counts[group] ?? 0)}</small>{/if}
    </button>
  {/each}
</nav>

<style>
  .style-filter{display:flex;gap:8px;overflow-x:auto;padding:12px 0 2px}
  button{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:transparent;border-radius:8px;padding:7px 12px;font-size:12px;white-space:nowrap;flex-shrink:0}
  small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
</style>
