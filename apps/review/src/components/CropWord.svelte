<script>
  import { wordChoices, setWord, clearWord } from '../lib/cropWords.js'
  import { t } from '../lib/i18n.svelte.js'
  import { signedInUser } from '../lib/session.svelte.js'

  let { item, disabled = false, working = null, saved = null } = $props()
  const METHODS = ['gloss', 'ruby', 'note', 'parallel', 'context']
  let editing = $state(false), query = $state(''), choices = $state([]), selected = $state(null)
  let method = $state(''), probable = $state(false), error = $state(''), pending = $state(false)
  const values = $derived(item.word?.values ?? [])
  // Only a reader's own claim can be cleared; the others stand until their readers take them back.
  const own = $derived(Boolean(signedInUser()?.id && item.word?.by?.includes(signedInUser().id)))

  $effect(() => {
    if (!editing) return
    const crop = item, q = query
    let active = true
    const timer = setTimeout(async () => {
      try {
        const found = await wordChoices(crop, q)
        if (active) choices = found
      } catch (e) { if (active) error = e.message }
    }, 200)
    return () => { active = false; clearTimeout(timer) }
  })

  function open() {
    query = ''; choices = []; selected = null; method = ''; probable = false; error = ''; editing = true
  }
  async function save(clear = false) {
    if (disabled || pending || (!clear && (!selected || !method))) return
    pending = true; working?.(true); error = ''
    try {
      const result = clear ? await clearWord(item) : await setWord(item, { word: selected, method, form: probable ? 'probable' : 'certain' })
      saved?.(result.crop)
      editing = false
    } catch (e) { error = e.message }
    finally { pending = false; working?.(false) }
  }
</script>

<div class="word-field">
  <div class="word-slot">
    <span>{t('word.label')}:</span>
    {#if values.length}
      {#each values as value, i}
        {#if i}<span> / </span>{/if}
        <b title={value.class ?? undefined}>{value.word ? value.reading ?? value.word : `「${value.gloss}」`}</b>
        {#if value.form === 'probable'}<span>{t('word.probable')}</span>{/if}
      {/each}
      {#if item.word.methods?.[0]}<span>{t(`word.methods.${item.word.methods[0]}`)}</span>{/if}
      {#if item.word.status === 'disputed'}<span>{t('word.disputed')}</span>{/if}
    {:else}<span>{t('word.none')}</span>{/if}
    {#if !editing}<button type="button" disabled={disabled || pending} onclick={open}>{t('word.set')}</button>{/if}
    {#if own}<button type="button" disabled={disabled || pending} onclick={() => save(true)}>{t('word.clear')}</button>{/if}
  </div>
  {#if editing}
    <div class="word-editor">
      <label>{t('word.search')}<input type="text" bind:value={query} oninput={() => { selected = null; choices = []; error = '' }} disabled={disabled || pending} /></label>
      <div class="word-choices">
        {#each choices as choice (choice.id)}
          <button type="button" aria-pressed={selected === choice.id} disabled={disabled || pending} onclick={() => selected = choice.id}>
            <b>{choice.reading}</b> <span>{choice.class}</span> <span>{choice.spellings.join('・')}</span>
          </button>
        {/each}
      </div>
      <label>{t('word.method')}
        <select bind:value={method} required disabled={disabled || pending}>
          <option value="" disabled>—</option>
          {#each METHODS as value}<option {value}>{t(`word.methods.${value}`)}</option>{/each}
        </select>
      </label>
      <label><input type="checkbox" bind:checked={probable} disabled={disabled || pending} />{t('word.probable')}</label>
      <div class="word-actions">
        <button type="button" disabled={disabled || pending || !selected || !method} onclick={() => save()}>{t('word.save')}</button>
        <button type="button" disabled={disabled || pending} onclick={() => { editing = false; error = '' }}>{t('word.cancel')}</button>
      </div>
    </div>
  {/if}
  {#if error}<span class="word-error" role="alert">{error}</span>{/if}
</div>

<style>
  .word-field{font-size:12px;color:var(--muted);display:grid;gap:6px}
  .word-slot,.word-actions{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
  b{color:var(--ink);font-weight:600}
  button,input,select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:4px 9px}
  button,select{cursor:pointer}
  button:hover:not(:disabled),select:hover:not(:disabled){border-color:var(--line-strong)}
  button[aria-pressed="true"]{border-color:var(--ink);background:var(--surface-raised,var(--surface))}
  button:disabled,input:disabled,select:disabled{opacity:.5;cursor:default}
  button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  .word-editor{display:grid;gap:8px;border:1px solid var(--line);border-radius:6px;padding:10px}
  label{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  input[type="text"]{flex:1;min-width:0}
  input[type="checkbox"]{accent-color:var(--ink)}
  .word-choices{display:flex;flex-wrap:wrap;gap:6px}
  .word-choices button{display:flex;flex-wrap:wrap;gap:6px;text-align:left}
  .word-choices span{color:var(--muted)}
  .word-error{color:var(--wrong)}
</style>
