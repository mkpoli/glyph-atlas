<script>
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  let { analysis = null, count = null, unassigned = null, value = '', onchange = () => {} } = $props()
  const groups = $derived(analysis?.groups ?? [])
</script>

{#if groups.length || unassigned > 0}
  <nav class="visual-groups" aria-label={t('visualGroups.label')}>
    <button class:active={!value} aria-pressed={!value} onclick={() => onchange('')}>
      <span>{t('visualGroups.allForms')}</span>{#if count != null}<small>{formatNumber(count)}</small>{/if}
    </button>
    {#each groups as group (group.id)}
      <button class="shape-group" class:active={value === group.id} aria-pressed={value === group.id}
              data-visual-group={group.id} onclick={() => onchange(group.id)}>
        <span class="group-examples">{#each (group.representatives ?? []).filter(sample => sample.image).slice(0, 3) as sample (sample.id)}
          <img src={sample.image} alt="" loading="lazy" />
        {/each}</span>
        <span>{group.label}{#if group.written_character}<small> ≈ {group.written_character}</small>{/if}</span><small>{formatNumber(group.count)}</small>
      </button>
    {/each}
    {#if unassigned > 0}<button class:active={value === 'unassigned'} aria-pressed={value === 'unassigned'} onclick={() => onchange('unassigned')}>
      <span>{t('corpus.unassigned')}</span><small>{unassigned}</small>
    </button>{/if}
  </nav>
{/if}

<style>
  .visual-groups{display:flex;gap:10px;overflow-x:auto;padding:16px 0 6px;align-items:stretch}
  button{display:flex;align-items:center;gap:10px;border:1px solid var(--line);background:transparent;border-radius:8px;padding:10px 14px;font-size:12px;white-space:nowrap;flex-shrink:0}
  small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .group-examples{display:flex;gap:3px}.group-examples:empty{display:none}
  .group-examples img{width:30px;height:38px;object-fit:contain;border-radius:2px}
</style>
