<script>
  // The written forms a run's graphemes gather, most frequent first, each with its count: ん𛁅 and ん𛁈
  // under んし. Choosing one narrows the occurrences to it; "all" shows every form. `forms` is
  // `[{ text, n }]`; a chosen form the list leaves out is shown after it.
  import ScriptText from './ScriptText.svelte'
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  let { forms = [], value = '', onchange = () => {} } = $props()
  const shown = $derived(value && !forms.some(f => f.text === value) ? [...forms, { text: value, n: null }] : forms)
  // The list stops at RUN_FORMS_MAX (24) forms; a full list may leave some out, so its sum is not shown.
  const total = $derived(forms.length < 24 ? forms.reduce((sum, f) => sum + f.n, 0) : null)
</script>

<nav class="form-filter" aria-label={t('run.forms.label')}>
  <span class="form-label">{t('run.forms.label')}</span>
  <button class:active={!value} aria-pressed={!value} onclick={() => onchange('')}>
    <span>{t('handFilter.all')}</span>{#if total != null}<small>{formatNumber(total)}</small>{/if}
  </button>
  {#each shown as f (f.text)}
    <button class:active={value === f.text} aria-pressed={value === f.text} onclick={() => onchange(f.text)}>
      <span class="form-text"><ScriptText text={f.text} /></span>{#if f.n != null}<small>{formatNumber(f.n)}</small>{/if}
    </button>
  {/each}
</nav>

<style>
  .form-filter{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px 0 2px}
  .form-label{font-size:12px;color:var(--muted);margin-inline-end:4px}
  button{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:transparent;border-radius:8px;padding:5px 12px;font-size:12px;white-space:nowrap;flex-shrink:0}
  .form-text{font-size:17px;line-height:1.2}
  small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
</style>
