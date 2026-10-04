<script>
  // The crop's form: the forms of its grapheme as chips, chosen with a click or a number key, and a
  // picker that adds another form to them: a character found by search, a listed variant or derived
  // form, or a shape written as an ideographic description sequence. A choice is held until the dialog
  // saves; choosing the form the crop already has drops it again.
  import FormChips from './FormChips.svelte'
  import FormText from './FormText.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsComposer from './IdsComposer.svelte'
  import { listForms, formOf } from '../lib/cropForms.js'
  import { t } from '../lib/i18n.svelte.js'
  // `others` leaves the crop's own form off the bar, where choosing a form marks the crop at once and
  // its own form would mark nothing.
  let { crop, chosen = null, onchoose, disabled = false, others = false } = $props()
  let offered = $state({ char: null, members: [], variants: [], derived: [] }), added = $state([])
  let picking = $state(false), query = $state(''), root = $state(null), failed = $state(false), addButton = $state(null)
  let composing = $state(false)
  const written = $derived(crop?.written_character ?? crop?.label ?? '')
  const current = $derived(formOf(crop))
  const BAR = 10
  $effect(() => {
    void crop?.id
    const char = written
    added = []; picking = false; query = ''; failed = false; composing = false
    listForms(char).then(found => { if (char === written) offered = { char, ...found } })
      .catch(() => { if (char === written) { offered = { char, members: [{ char }], variants: [], derived: [] }; failed = true } })
  })
  const listed = $derived(offered.char === written ? offered : { members: [{ char: written }], variants: [], derived: [] })
  // The bar: the grapheme's first forms, the crop's own form when it is none of them, and what the
  // picker added.
  const bar = $derived.by(() => {
    const chips = listed.members.filter(member => !others || member.char !== current).slice(0, BAR)
    const has = char => chips.some(chip => chip.char === char)
    if (current && !others && !has(current)) chips.push({ char: current, script: '' })
    for (const form of added) if (!has(form.char)) chips.push(form)
    return chips
  })
  // The bar wraps onto further rows; only a grapheme's forms past the first ten wait in the picker.
  const rest = $derived(listed.members.slice(BAR))
  const q = $derived(query.trim().toUpperCase())
  const matches = item => !q || item.char.toUpperCase().includes(q) || (item.code_point ?? '').includes(q)
  const sections = $derived([
    ['grapheme', t('chips.grapheme'), rest],
    ['variants', t('chips.variants'), listed.variants],
    ['derived', t('chips.derived'), listed.derived],
  ].map(([key, title, items]) => [key, title, items.filter(matches)]).filter(([, , items]) => items.length))
  function choose(char) { onchoose(char === current ? null : char) }
  function add(item) {
    if (!bar.some(chip => chip.char === item.char)) added = [...added, { char: item.char, code_point: item.code_point, script: item.script ?? '' }]
    picking = false; query = ''; composing = false
    onchoose(item.char === current ? null : item.char)
  }
  // A derived option names how it was made: each substitution of its first route, as made.
  const optionTitle = item => item.routes?.length
    ? `${item.char}\n` + item.routes[0].map(sub => `${sub.was} → ${sub.became}`).join('\n')
    : item.code_point ?? item.char
  // 1–0 choose the bar's forms, as Quick Review's form bar does; a key typed into a field is the field's.
  function keydown(event) {
    if (disabled || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
    const dialog = [...document.querySelectorAll('dialog[open]')].at(-1)
    if (dialog ? !dialog.contains(root) : root.closest('dialog')) return
    // Escape closes the picker, from its search too, before it can close the dialog.
    if (event.key === 'Escape' && picking) { event.preventDefault(); picking = false; query = ''; composing = false; addButton?.focus(); return }
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"], .character-search')) return
    const index = '1234567890'.indexOf(event.key)
    if (index >= 0 && bar[index]) { event.preventDefault(); choose(bar[index].char) }
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="crop-form" bind:this={root}>
  <div class="form-row">
    <FormChips forms={bar} {chosen} {current} {disabled} label={t('quiz.forms.label', { char: written })} onchoose={choose} />
    <button type="button" class="form-add" bind:this={addButton} {disabled} aria-expanded={picking} aria-label={t('form.add')} title={t('form.add')} onclick={() => picking = !picking}>{rest.length ? `+${rest.length}` : '+⌕'}</button>
  </div>
  {#if picking}
    <div class="form-picker" role="dialog" aria-label={t('form.add')}>
      <CharacterSearch compact codePoints autofocus bind:value={query} label={t('form.add')} placeholder={t('search.placeholder')} onselect={add} />
      {#each sections as [key, title, items] (key)}
        <div class="form-section">
          <span class="form-section-title">{title}</span>
          <div class="form-options">
            {#each items as item (item.char)}
              <button type="button" class="form-option" {disabled} title={optionTitle(item)} onclick={() => add(item)}>
                <FormText text={item.char} script={item.script} />{#if item.encoded !== false && item.code_point}<small>{item.code_point}</small>{/if}
              </button>
            {/each}
          </div>
        </div>
      {/each}
      {#if composing}
        <IdsComposer {disabled} start={query.trim()} char={written} onuse={char => add({ char })} />
      {:else}
        <button type="button" class="form-compose" {disabled} onclick={() => composing = true}>{t('form.ids.compose')}</button>
      {/if}
      {#if failed}<p class="form-note" role="status">{t('layers.readError')}</p>{/if}
    </div>
  {/if}
</div>

<style>
  .crop-form{display:flex;flex-direction:column;gap:10px;margin:0 0 16px}
  .form-row{display:flex;gap:6px;align-items:flex-start}
  .crop-form :global(.form-chips){flex:1 1 0}
  .form-add{flex:none;min-width:40px;padding:4px 10px;font-size:20px;line-height:1.2;color:var(--muted);border-style:dashed}
  .form-add[aria-expanded="true"]{color:var(--accent);border-color:var(--accent)}
  .form-picker{display:flex;flex-direction:column;gap:12px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
  .form-picker :global(.character-search){width:100%;min-width:0}
  .form-section{display:flex;flex-direction:column;gap:6px}
  .form-section-title{font-size:11px;color:var(--muted)}
  .form-options{display:flex;flex-wrap:wrap;gap:6px}
  .form-option{display:inline-flex;flex-direction:column;align-items:center;gap:2px;min-width:44px;padding:5px 9px;font-size:21px;line-height:1.2;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif}
  .form-option small{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  .form-option:not(:disabled):hover{border-color:var(--accent)}
  .form-compose{align-self:flex-start;font-size:13px;padding:4px 10px}
  .form-note{margin:0;font-size:11px;color:var(--muted)}
</style>
