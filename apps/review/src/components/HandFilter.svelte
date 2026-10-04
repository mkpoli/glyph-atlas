<script>
  // A run's occurrences by how their letterforms were made (`lib/hand.js`): every group with its count, and
  // the productions each holds on hover.
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  import { HAND_GROUPS, PRODUCTIONS_IN } from '../lib/hand.js'
  let { counts = null, value = '', onchange = () => {} } = $props()
  const total = $derived(counts ? HAND_GROUPS.reduce((sum, group) => sum + (counts[group] ?? 0), 0) : null)
  // A production's label key spells its path with underscores (`printed/type` is `printed_type`).
  const kindKey = kind => kind.split('/').join('_')
  const members = group => [...PRODUCTIONS_IN[group].map(kind => t(`production.kind.${kindKey(kind)}`)),
    ...(group === 'hand' ? [t('styleFilter.cursive')] : [])].join(' · ')
</script>

<nav class="hand-filter" aria-label={t('handFilter.label')}>
  <button class:active={!value} aria-pressed={!value} onclick={() => onchange('')}>
    <span>{t('handFilter.all')}</span>{#if total != null}<small>{formatNumber(total)}</small>{/if}
  </button>
  {#each HAND_GROUPS as group (group)}
    <button class:active={value === group} aria-pressed={value === group} data-hand={group} title={members(group)} onclick={() => onchange(group)}>
      <span>{t(`handFilter.${group}`)}</span>{#if counts}<small>{formatNumber(counts[group] ?? 0)}</small>{/if}
    </button>
  {/each}
</nav>

<style>
  .hand-filter{display:flex;flex-wrap:wrap;gap:8px;padding:12px 0 2px}
  button{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:transparent;border-radius:8px;padding:7px 12px;font-size:12px;white-space:nowrap;flex-shrink:0}
  small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
</style>
