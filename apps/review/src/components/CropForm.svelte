<script>
  // The crop's form: the forms of its grapheme and related characters as chips — its members, its
  // variants and its related characters — chosen with a click or a number key, and a picker that
  // adds another form to them: a derived character, a character found by search, or a shape written
  // as an ideographic description sequence. A choice is held until the dialog saves. Every chip toggles:
  // a click chooses it, the crop's own form too, which confirms that form, and a click on the chosen
  // chip drops the choice.
  // A described form or a registered figure a crop carries is one of its grapheme's forms, on every bar
  // of the family.
  // The forms stand under their 構形, on the bar: 説 and 說 under one, 峰 and 峯 under two, a figure
  // of the IVD with its character. A form with no structure, and a grapheme of another script, stand
  // last without a caption. A small seal character stands last in its 構形. A form's 構形 is read before
  // the form is shown, so the chips, and the number keys with them, keep their places.
  // A crop nobody has assigned a character takes the forms of its whole grapheme (`graphemeChips`), none of
  // them its own; a bar of many folds the 構形 after the first away behind one chip. What choosing a form
  // assigns is known once they are read, so until then, and where they cannot be read, it offers none.
  // Where no form names the crop's shape, its 構形 alone can be given and the form left for later: the ？ at
  // the end of its character's own 構形, and any 構形 of the bar from the picker.
  // A signed-in reader opens the 構形 of the bar's forms to arrange them, where a form, typed, derived
  // or encoded, is filed under another 構形.
  import { untrack } from 'svelte'
  import FormChips from './FormChips.svelte'
  import FormText from './FormText.svelte'
  import IdsLabel from './IdsLabel.svelte'
  import CopyValue from './CopyValue.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsComposer from './IdsComposer.svelte'
  import StructureDialog from './StructureDialog.svelte'
  import { baseOf, builtAs, listForms, listGrapheme, formOf } from '../lib/cropForms.js'
  import { proposed, standing, structureTarget } from '../lib/structures.js'
  import { chipsOf, groupForms } from '../lib/formOffers.js'
  import { NO_FORMS, foldGroups, listedOf } from '../lib/graphemeBar.js'
  import { graphemeChar, isUnassigned } from '../lib/identity.js'
  import { signedInUser } from '../lib/session.svelte.js'
  import { t } from '../lib/i18n.svelte.js'
  // `others` leaves the crop's own form off the bar, where choosing a form marks the crop at once and
  // its own form would mark nothing. `onchoose` is given the form, and the character choosing it names.
  let { crop, chosen = null, onchoose, disabled = false, others = false } = $props()
  let offered = $state({ char: null, whole: false, ...NO_FORMS }), added = $state([]), attempt = $state(0)
  let picking = $state(false), query = $state(''), root = $state(null), failed = $state(false), addButton = $state(null)
  let composing = $state(false)
  let arranging = $state(false), expanded = $state(false)
  const unassigned = $derived(isUnassigned(crop))
  // The character whose forms are asked for: the crop's own, or its grapheme's where it has none.
  const asked = $derived(unassigned ? graphemeChar(crop) ?? '' : crop?.written_character ?? crop?.label ?? '')
  const list = char => unassigned ? listGrapheme(char, [crop?.source_label, crop?.label]) : listForms(char)
  const current = $derived(formOf(crop))
  // The 構形 each form stands under, by the form's text; a form the site knows no structure of has
  // none. The crop's character is named too, for its regional shapes.
  let structureOf = $state(new Map())
  const HAN = /^\p{Script=Han}$/u
  // The characters whose regional shapes are among a bar's forms.
  const shapedIn = (found, char) => found.shaped ? found.shaped.join('') : HAN.test(char) ? char : ''
  const stands = (names, known, of = shapedIn(listed, asked)) => standing(names, known, of)
  const composed = (names, known = new Map(), of) => stands(names, known, of).catch(() => known)
  const formsIn = found => found.chips ?? [...found.members, ...found.variants, ...found.related]
  $effect(() => {
    const id = crop?.id, char = asked, whole = unassigned, own = untrack(() => current)
    attempt
    added = []; picking = false; query = ''; failed = false; composing = false; arranging = false; expanded = false
    list(char).then(found => [found, false], () => [whole ? null : { ...NO_FORMS, members: [{ char }] }, true])
      .then(async ([found, unread]) => {
        const read = found ? await composed([own, ...formsIn(found).map(form => form.char)], new Map(), shapedIn(found, char)) : new Map()
        if (id !== crop?.id || char !== asked || whole !== unassigned) return
        if (found) { structureOf = read; offered = { char, whole, ...found } }
        failed = unread
      })
  })
  const { ready, listed } = $derived(listedOf(offered, asked, unassigned))
  // The character the forms stand around: the crop's own, or its grapheme's head.
  const written = $derived(listed.of ?? asked)
  // The grapheme's forms and related characters: its members, its variants, and its related characters, which
  // the picker holds for a whole grapheme.
  const forms = $derived(formsIn(listed).filter(form => !others || form.char !== current))
  // The bar: the grapheme's forms and related characters, the crop's own form when it is none of them
  // (a form drawn as the bare character keeps its regions), and what the picker added.
  const bar = $derived.by(() => {
    if (!ready) return []
    const chips = [...forms]
    const has = char => chips.some(chip => chip.char === char)
    if (current && !others && !has(current)) chips.push(listed.alike.find(form => form.char === current) ?? { char: current, script: '', lang: '' })
    for (const form of added) if (!has(form.char)) chips.push(form)
    return chips
  })
  // The 構形 of the crop's own form, which Quick Review's bar leaves off.
  const ownStructure = $derived(structureOf.get(current) ?? null)
  const relatedChars = $derived(new Set(listed.chips ? [] : listed.related.map(form => form.char)))
  const me = $derived(signedInUser()?.id ?? null)
  const barGroups = $derived(groupForms(bar, { structureOf, related: relatedChars, own: (ownStructure?.part ?? ownStructure)?.id ?? builtAs(crop)[0]?.structure, proposed }))
  // What a whole grapheme's bar folds away: never a form its source names, the crop has, or the reader chose.
  const folded = $derived(listed.chips ? foldGroups(barGroups, { keep: new Set([...listed.focal, current, typeof chosen === 'string' ? chosen : '', ...added.map(form => form.char)]) }) : { shown: barGroups, hidden: [] })
  const shownGroups = $derived(expanded ? barGroups : folded.shown)
  const hidden = $derived(chipsOf(folded.hidden).length)
  const captioned = $derived(shownGroups.some(group => group.label))
  // The bar's forms in the order the number keys reach them.
  const ordered = $derived(chipsOf(shownGroups))
  const q = $derived(query.trim().toUpperCase())
  const matches = item => (!q || item.char.toUpperCase().includes(q) || (item.code_point ?? '').includes(q)) && !bar.some(chip => chip.char === item.char)
  const sections = $derived([
    ...listed.chips ? [['related', t('chips.related'), listed.related]] : [],
    ['derived', t('chips.derived'), listed.derived],
  ].map(([key, title, items]) => [key, title, items.filter(matches)]).filter(([, , items]) => items.length))
  const give = char => onchoose(char, baseOf(char, listed))
  function choose(char) { if (char === chosen) onchoose(null); else give(char) }
  // The 構形 the crop is said to be built as, and the one chosen here (`{ structure, label, target }`; with
  // no `target` the reader's own word is taken back).
  const built = $derived(builtAs(crop))
  const mineBuilt = $derived(me ? (crop?.structure?.values ?? []).find(value => value.by.includes(me)) : null)
  // The 構形 named under the bar: what the crop is built as, and the reader's own word while a form stands.
  const builtShown = $derived(built.length ? built : mineBuilt ? [mineBuilt] : [])
  const open = $derived(chosen && typeof chosen === 'object' ? chosen : null)
  function leaveOpen(block) { onchoose(open?.structure === block.id ? null : { structure: block.id, label: block.label, target: targets.get(block.id) }) }
  // The bar's 構形 a save can name, by id.
  const targets = $derived(new Map(others ? [] : barGroups.flatMap(group => group.blocks.filter(block => block.label).map(block => [block.id, structureTarget(group, block, structureOf, written)])).filter(([, target]) => target)))
  const blocks = $derived(barGroups.flatMap(group => group.blocks).filter(block => targets.has(block.id)))
  const shapes = $derived(new Map(blocks.filter(block => !unassigned && block.forms.some(form => form.char === written) || built.some(value => value.structure === block.id) || open?.structure === block.id)
    .map(block => [block.id, { title: t('form.structureOnly.title', { structure: block.label }), chosen: open?.structure === block.id, current: !chosen && built.some(value => value.structure === block.id) }])))
  // What the 構形 dialog lays out, and the bar reads again after it closes on a change.
  const arranged = $derived(bar)
  async function rearranged() {
    const id = crop?.id
    const found = await composed(arranged.map(form => form.char), structureOf)
    if (id === crop?.id) structureOf = found
  }
  async function add(item) {
    const id = crop?.id
    picking = false; query = ''; composing = false
    // Where choosing marks at once, the crop's own form, found by search, would mark nothing.
    if (others && item.char === current) return
    if (!bar.some(chip => chip.char === item.char)) {
      const read = await composed([item.char], structureOf)
      if (id !== crop?.id) return
      structureOf = read
      added = [...added, { char: item.char, code_point: item.code_point, script: item.script ?? '', lang: item.lang ?? '' }]
    }
    give(item.char)
  }
  // A derived option names how it was made: each substitution of its first route, as made; a
  // registered figure names its sequence and its MJ figure; a regional shape names its regions.
  const optionTitle = item => item.routes?.length
    ? `${item.char}\n` + item.routes[0].map(sub => `${sub.was} → ${sub.became}`).join('\n')
    : item.mj ? `${item.code_point}\n${item.mj}`
    : item.regions ? `${item.char}\n${item.regions}` : item.code_point ?? item.char
  // A registered figure on the bar names its MJ figure, and a regional shape its regions, since either
  // can look like the character itself.
  const chipTitle = char => { const form = bar.find(chip => chip.char === char); return form?.mj ? `${char} ${form.mj}` : form?.regions ? `${char} ${form.regions}`
    : proposed(char, structureOf.get(char)?.by) && structureOf.has(char) ? `${char} · ${t('chips.derived')}` : char }
  // 1–0 choose the bar's forms, as Quick Review's form bar does; a key typed into a field is the field's.
  function keydown(event) {
    if (disabled || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
    const dialog = [...document.querySelectorAll('dialog[open]')].at(-1)
    if (dialog ? !dialog.contains(root) : root.closest('dialog')) return
    // Escape closes the picker, from its search too, before it can close the dialog.
    if (event.key === 'Escape' && picking) { event.preventDefault(); picking = false; query = ''; composing = false; addButton?.focus(); return }
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"], .character-search, .ids-composer')) return
    const index = '1234567890'.indexOf(event.key)
    if (index >= 0 && ordered[index]) { event.preventDefault(); choose(ordered[index].char) }
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="crop-form" bind:this={root}>
  <div class="form-row copyable" class:captioned>
    <FormChips groups={shownGroups} open={shapes} onopen={leaveOpen} {chosen} {current} {disabled} label={unassigned ? t('corpus.assign.label') : t('quiz.forms.label', { char: written })} title={chipTitle} onchoose={choose}>
      {#if !others}<CopyValue value={open ? open.label ?? written : chosen ?? (current || (unassigned ? '' : written))} />{/if}
    {#if !ready && failed}<p class="form-unread" role="alert">{t('layers.readError')}<button type="button" class="quiet-link" onclick={() => attempt++}>{t('common.retry')}</button></p>{/if}
      {#if hidden}{@const more = `${t('explore.grapheme.forms', { count: hidden })} · ${t('chips.structure')} ${folded.hidden.length}`}
        <button type="button" class="form-more" aria-expanded={expanded} onclick={() => expanded = !expanded}>{expanded ? '−' : `+${hidden}`}<small>{more}</small></button>{/if}
    </FormChips>
    <button type="button" class="form-add" bind:this={addButton} disabled={disabled || !ready} aria-expanded={picking} aria-label={t('form.add')} title={t('form.add')} onclick={() => picking = !picking}>+⌕</button>
  </div>
  {#if others && ownStructure}
    <div class="form-own"><span class="form-own-label">{t('chips.structure')}</span><span><IdsLabel text={(ownStructure.part ?? ownStructure).label} />{#if proposed(current, ownStructure.by)}<sup title={t('chips.derived')}>*</sup>{/if}</span></div>
  {/if}
  {#if builtShown.length && !others}
    <div class="form-built" class:dropped={open && !open.target}><span class="form-own-label">{t('chips.structure')}</span>
      {#each builtShown as value, i (value.structure)}{#if i} / {/if}<IdsLabel text={value.label} />{/each}
      {#if mineBuilt}<button type="button" class="form-drop" {disabled} aria-pressed={Boolean(open && !open.target)} onclick={() => onchoose(open && !open.target ? null : { structure: null, label: null, target: null })}
        aria-label={t('form.structureOnly.remove', { structure: mineBuilt.label })} title={t('form.structureOnly.remove', { structure: mineBuilt.label })}>×</button>{/if}
    </div>
  {/if}
  {#if me && !others && captioned}
    <button type="button" class="form-arrange" {disabled} aria-haspopup="dialog" onclick={() => arranging = true}>{t('structure.arrange')}</button>
  {/if}
  {#if arranging}
    <StructureDialog of={written} forms={arranged} known={structureOf} read={stands} current={open ? current : chosen ?? current} close={() => arranging = false} changed={rearranged} />
  {/if}
  {#if picking}
    <div class="form-picker" role="dialog" aria-label={t('form.add')}>
      <CharacterSearch compact codePoints autofocus bind:value={query} label={t('form.add')} placeholder={t('search.placeholder')} onselect={add} />
      {#each sections as [key, title, items] (key)}
        <div class="form-section">
          <span class="form-section-title">{title}</span>
          <div class="form-options">
            {#each items as item (item.char)}
              <button type="button" class="form-option" {disabled} title={optionTitle(item)} onclick={() => add(item)}>
                <FormText text={item.char} script={item.script} lang={item.lang} />{#if item.mj}<small>{item.mj}</small>{:else if item.encoded !== false && item.code_point}<small>{item.code_point}</small>{:else if item.regions}<small>{item.regions}</small>{/if}
              </button>
            {/each}
          </div>
        </div>
      {/each}
      {#if blocks.length && !q}
        <div class="form-section">
          <span class="form-section-title">{t('form.structureOnly')}</span>
          <div class="form-options">
            {#each blocks as block (block.id)}
              <button type="button" class="form-option form-structure" class:chosen={open?.structure === block.id} {disabled} aria-pressed={open?.structure === block.id}
                title={t('form.structureOnly.title', { structure: block.label })} onclick={() => { picking = false; leaveOpen(block) }}><span lang="zh">{block.label}</span></button>
            {/each}
          </div>
        </div>
      {/if}
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
  .form-row :global(.copy-value){align-self:center}
  /* A caption stands above each group's chips: the controls beside them keep to the chips' line. */
  .form-row.captioned .form-add{margin-top:17px}
  .form-row.captioned :global(.copy-value){align-self:flex-end;margin-bottom:8px}
  .form-own{display:flex;align-items:baseline;gap:8px;font-size:14px;line-height:22px}
  .form-own sup{font-size:11px;line-height:1;color:var(--muted);margin-left:2px}
  .form-own-label{font-size:11px;color:var(--muted)}
  .crop-form :global(.form-chips){flex:1 1 0}
  .form-more{display:inline-flex;align-items:baseline;gap:8px;align-self:flex-end;padding:4px 10px;font-size:15px;line-height:1.75;border-style:dashed;color:var(--muted)}
  .form-unread{display:flex;flex-wrap:wrap;gap:2px 10px;align-items:baseline;align-self:center;margin:0;font-size:12px;color:var(--muted)}
  .form-more small{font-size:11px}
  .form-add{flex:none;min-width:40px;padding:4px 10px;font-size:20px;line-height:1.2;color:var(--muted);border-style:dashed}
  .form-add[aria-expanded="true"]{color:var(--accent);border-color:var(--accent)}
  .form-picker{display:flex;flex-direction:column;gap:12px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
  .form-picker :global(.character-search){width:100%;min-width:0}
  .form-built{display:flex;align-items:baseline;gap:8px;font-size:14px;line-height:22px}
  .form-drop{border:0;background:transparent;color:var(--muted);padding:0 2px;font-size:15px;line-height:1}
  .form-drop:not(:disabled):hover{color:var(--wrong)}
  .form-built.dropped :global(.ids-label){text-decoration:line-through;color:var(--muted)}
  .form-option.form-structure{font-family:var(--char-font);font-size:15px;border-style:dashed}
  .form-section{display:flex;flex-direction:column;gap:6px}
  .form-section-title{font-size:11px;color:var(--muted)}
  .form-options{display:flex;flex-wrap:wrap;gap:6px}
  .form-option{display:inline-flex;flex-direction:column;align-items:center;gap:2px;min-width:44px;padding:5px 9px;font-size:21px;line-height:1.2;
    font-family:var(--char-font)}
  .form-option small{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  .form-option:not(:disabled):hover{border-color:var(--accent)}
  .form-compose{align-self:flex-start;font-size:13px;padding:4px 10px}
  .form-note{margin:0;font-size:11px;color:var(--muted)}
  .form-arrange{border:0;background:transparent;color:var(--accent);padding:0;font:inherit;font-size:12px;text-align:left;
    text-decoration:underline;text-underline-offset:3px}
  .form-arrange:not(:disabled):hover{color:var(--ink)}
  .form-arrange{align-self:flex-start;line-height:1.4}
</style>
