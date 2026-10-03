<script>
  // A gallery's crops by the date of their books: in style order or oldest first, and narrowed to one
  // hundred years or to the books no source gives a year (some name only a period, such as 江戸後期). `counts` is the gallery's decades, [decade, crops]
  // with decade null for the undated, the collection's and the corpus's added together.
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  import { dateLabel } from '../lib/dating.js'
  let { counts = null, value = '', order = '', onchange = () => {}, onorder = () => {} } = $props()
  const hundreds = $derived.by(() => {
    const found = new Map()
    for (const [decade, n] of counts ?? []) if (decade !== null) {
      const start = Math.floor(decade / 100) * 100
      found.set(start, (found.get(start) ?? 0) + n)
    }
    return [...found].sort((a, b) => a[0] - b[0])
  })
  const undated = $derived((counts ?? []).filter(([decade]) => decade === null).reduce((sum, [, n]) => sum + n, 0))
  const total = $derived((counts ?? []).reduce((sum, [, n]) => sum + n, 0))
  const range = start => `${start}-${start + 99}`
  // A range the chips do not name (a decade an axis linked to, or one this gallery has nothing in).
  const other = $derived.by(() => {
    const found = /^(-?\d+)-(-?\d+)$/.exec(value)
    if (!found || hundreds.some(([start]) => range(start) === value)) return null
    return dateLabel({ start: Number(found[1]), end: Number(found[2]), precision: 'years' })
  })
  const label = start => dateLabel({ start, end: start + 99, precision: 'years' })
</script>

<div class="period-filter">
  <nav class="orders" aria-label={t('period.order.label')}>
    <button class:active={order !== 'year'} aria-pressed={order !== 'year'} onclick={() => onorder('')}>{t('period.order.style')}</button>
    <button class:active={order === 'year'} aria-pressed={order === 'year'} onclick={() => onorder('year')}>{t('period.order.year')}</button>
  </nav>
  <nav class="periods" aria-label={t('period.label')}>
    <button class:active={!value} aria-pressed={!value} onclick={() => onchange('')}>
      <span>{t('period.all')}</span>{#if counts}<small>{formatNumber(total)}</small>{/if}
    </button>
    {#each hundreds as [start, n] (start)}
      <button class:active={value === range(start)} aria-pressed={value === range(start)} onclick={() => onchange(range(start))}>
        <span>{label(start)}</span><small>{formatNumber(n)}</small>
      </button>
    {/each}
    {#if other}<button class="active" aria-pressed="true" onclick={() => onchange('')}><span>{other}</span></button>{/if}
    {#if undated || value === 'undated'}
      <button class:active={value === 'undated'} aria-pressed={value === 'undated'} onclick={() => onchange('undated')}>
        <span>{t('date.noYear')}</span><small>{formatNumber(undated)}</small>
      </button>
    {/if}
  </nav>
</div>

<style>
  .period-filter{display:flex;align-items:center;gap:16px;padding:10px 0 2px;min-width:0}
  nav{display:flex;gap:8px;overflow-x:auto;min-width:0}
  .orders{flex-shrink:0;gap:0;border:1px solid var(--line);border-radius:8px;padding:2px}
  .orders button{border:0;border-radius:6px;padding:5px 10px}
  button{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:transparent;border-radius:8px;padding:7px 12px;font-size:12px;white-space:nowrap;flex-shrink:0}
  small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .periods .active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .orders .active{background:var(--surface-selected);color:var(--ink)}
  @media(max-width:700px){.period-filter{flex-direction:column;align-items:stretch;gap:8px}.orders{align-self:flex-start}}
</style>
