<script>
  // The crop's form: the forms of its grapheme and related characters as chips — its members, its
  // variants and its related characters — chosen with a click or a number key, and a picker that
  // adds another form to them: a derived character, a character found by search, or a shape written
  // as an ideographic description sequence. A choice is held until the dialog saves. Every chip toggles:
  // a click chooses it, the crop's own form too, which confirms that form, and a click on the chosen
  // chip drops the choice.
  // A described form or a registered figure the crop already has can be recorded as written for its
  // grapheme (written_for), so the form joins the grapheme's 字体 on every card and in every picker of
  // the family.
  // The forms stand under their 構形, on the bar: 説 and 說 under one, 峰 and 峯 under two, a figure
  // of the IVD with its character. A form with no structure, and a grapheme of another script, stand
  // last without a caption. A form's 構形 is read before the form is shown, so the chips, and the
  // number keys with them, keep their places.
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
  import { listForms, formOf, setWrittenFor } from '../lib/cropForms.js'
  import { proposed, standing } from '../lib/structures.js'
  import { formsOf } from '../lib/formOffers.js'
  import { signedInUser } from '../lib/session.svelte.js'
  import { t } from '../lib/i18n.svelte.js'
  // `others` leaves the crop's own form off the bar, where choosing a form marks the crop at once and
  // its own form would mark nothing.
  let { crop, chosen = null, onchoose, disabled = false, others = false } = $props()
  let offered = $state({ char: null, members: [], variants: [], related: [], derived: [], linked: [], grapheme: null, alike: [] }), added = $state([])
  let picking = $state(false), query = $state(''), root = $state(null), failed = $state(false), addButton = $state(null)
  let composing = $state(false), linking = $state(false), linkError = $state(''), linkedByMe = $state(false)
  let arranging = $state(false)
  const written = $derived(crop?.written_character ?? crop?.label ?? '')
  const current = $derived(formOf(crop))
  // The 構形 each form stands under, by the form's text; a form the site knows no structure of has
  // none. The crop's character is named too, for its regional shapes.
  let structureOf = $state(new Map())
  const HAN = /^\p{Script=Han}$/u
  const stands = (names, known) => standing(names, known, HAN.test(written) ? written : '')
  const composed = (names, known = new Map()) => stands(names, known).catch(() => known)
  const namesOf = found => [...found.members, ...found.variants, ...found.related].map(form => form.char)
  $effect(() => {
    const id = crop?.id, char = written, own = untrack(() => current)
    added = []; picking = false; query = ''; failed = false; composing = false; linking = false; linkError = ''; linkedByMe = false; arranging = false
    listForms(char).then(found => [found, false], () => [{ members: [{ char }], variants: [], related: [], derived: [], linked: [], grapheme: null, alike: [] }, true])
      .then(async ([found, unread]) => {
        const read = await composed([own, ...namesOf(found)])
        if (id === crop?.id && char === written) { structureOf = read; offered = { char, ...found }; failed = unread }
      })
  })
  const listed = $derived(offered.char === written ? offered : { members: [{ char: written }], variants: [], related: [], derived: [], linked: [], grapheme: null, alike: [] })
  // The grapheme's forms and related characters: its members, its variants, and its related characters.
  const forms = $derived([...listed.members, ...listed.variants, ...listed.related].filter(form => !others || form.char !== current))
  // The bar: the grapheme's forms and related characters, the crop's own form when it is none of them
  // (a form drawn as the bare character keeps its regions), and what the picker added.
  const bar = $derived.by(() => {
    const chips = [...forms]
    const has = char => chips.some(chip => chip.char === char)
    if (current && !others && !has(current)) chips.push(listed.alike.find(form => form.char === current) ?? { char: current, script: '', lang: '' })
    for (const form of added) if (!has(form.char)) chips.push(form)
    return chips
  })
  // The 構形 of the crop's own form, which Quick Review's bar leaves off.
  const ownStructure = $derived(structureOf.get(current) ?? null)
  // A list's forms under the 構形 each stands under, those built as a 構形 folded into it (⿰辵禺 under
  // ⿺辶禺) in a block of their own, a structure where its first form stands; the forms
  // with none stand together after them, uncaptioned. The grapheme's own forms come first; a related
  // character joins a 構形 of theirs where it shares one, and otherwise stands after all of them, so
  // the number keys reach the grapheme's forms before any related character.
  const relatedChars = $derived(new Set(listed.related.map(form => form.char)))
  const grouped = list => {
    const own = ownStructure && (ownStructure.part ?? ownStructure).id
    const block = ({ id, label }) => ({ id, label, own: id === own, forms: [] })
    const groups = new Map(), loose = [], later = new Map(), looseLater = []
    const place = (form, into, rest) => {
      const structure = structureOf.get(form.char)
      if (!structure) { rest.push(form); return }
      form = { ...form, proposed: proposed(form.char, structure.by) }
      let home = groups.get(structure.id) ?? into.get(structure.id)
      if (!home) into.set(structure.id, home = { id: structure.id, label: structure.label, blocks: [block(structure)] })
      let built = home.blocks.find(one => one.id === (structure.part ?? structure).id)
      if (!built) home.blocks.push(built = block(structure.part))
      built.forms.push(form)
    }
    for (const form of list) if (!relatedChars.has(form.char)) place(form, groups, loose)
    for (const form of list) if (relatedChars.has(form.char)) place(form, later, looseLater)
    // A layout with a form of the grapheme stands before one with related characters alone.
    const rank = built => built.forms.length && built.forms.every(form => relatedChars.has(form.char)) ? 1 : 0
    for (const group of groups.values()) group.blocks.sort((a, b) => rank(a) - rank(b))
    const plain = (id, forms) => forms.length ? [{ id, label: '', blocks: [{ id, label: '', own: false, forms }] }] : []
    return [...groups.values(), ...plain('', loose), ...later.values(), ...plain(' related', looseLater)]
  }
  const me = $derived(signedInUser()?.id ?? null)
  const barGroups = $derived(grouped(bar))
  const captioned = $derived(barGroups.some(group => group.label))
  // The bar's forms as they stand, which the number keys follow.
  const ordered = $derived(barGroups.flatMap(formsOf))
  // The crop's own form as the site holds it: one value, and not disputed. A described form or a
  // registered figure (a variation sequence) can be added to the grapheme's 字体 here; encoded
  // characters already have a card row of their own. A figure the bar offers from the IVD is not on
  // the card until someone adds it.
  const saved = $derived(crop?.form?.status === 'disputed' ? null : (crop?.form?.values ?? []).find(value => value.form) ?? null)
  const linkedChars = $derived(new Set((listed.linked ?? []).map(item => item.char)))
  const linkedHere = $derived(Boolean(saved && linkedChars.has(saved.text)))
  // A form drawn as the bare character is known too: it needs no link to be found.
  const knownForm = $derived(Boolean(saved && [...listed.members, ...listed.variants, ...listed.alike].some(item => item.char === saved.text && !item.mj && !linkedChars.has(item.char))))
  const grapheme = $derived(listed.grapheme?.code_point ?? crop?.grapheme ?? null)
  const canLink = $derived(Boolean(saved && (saved.scheme === 'ids' || saved.scheme === 'ivs') && !linkedHere && !knownForm && !chosen && grapheme && !disabled && !failed))
  const q = $derived(query.trim().toUpperCase())
  const matches = item => (!q || item.char.toUpperCase().includes(q) || (item.code_point ?? '').includes(q)) && !bar.some(chip => chip.char === item.char)
  const sections = $derived([
    ['derived', t('chips.derived'), listed.derived],
  ].map(([key, title, items]) => [key, title, items.filter(matches)]).filter(([, , items]) => items.length))
  function choose(char) { onchoose(char === chosen ? null : char) }
  // The grapheme's forms changed for every crop of its family, so the card is read again after a save.
  async function reload() {
    const id = crop?.id, char = written
    try {
      const found = await listForms(char)
      const read = found ? await composed(namesOf(found), structureOf) : structureOf
      if (found && id === crop?.id && char === written) { structureOf = read; offered = { char, ...found } }
    } catch { /* the save stands; the card reads again next time */ }
  }
  // What the 構形 dialog lays out, and the bar reads again after it closes on a change.
  const arranged = $derived(bar)
  async function rearranged() {
    const id = crop?.id
    const found = await composed(arranged.map(form => form.char), structureOf)
    if (id === crop?.id) structureOf = found
  }
  async function link() {
    if (!canLink || linking) return
    linking = true; linkError = ''
    try {
      const result = await setWrittenFor(saved.form, grapheme)
      linkedByMe = Boolean(me && (result?.written_for ?? []).find(value => value.grapheme === grapheme)?.by?.includes(me))
      await reload()
    } catch (error) { linkError = error.message }
    finally { linking = false }
  }
  async function unlink() {
    if (linking) return
    linking = true; linkError = ''
    try {
      await setWrittenFor(saved.form, grapheme, true)
      linkedByMe = false
      await reload()
    } catch (error) { linkError = error.message }
    finally { linking = false }
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
    onchoose(item.char)
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
    <FormChips groups={barGroups} {chosen} {current} {disabled} label={t('quiz.forms.label', { char: written })} title={chipTitle} onchoose={choose}>
      {#if !others}<CopyValue value={chosen ?? current} />{/if}
    </FormChips>
    <button type="button" class="form-add" bind:this={addButton} {disabled} aria-expanded={picking} aria-label={t('form.add')} title={t('form.add')} onclick={() => picking = !picking}>+⌕</button>
  </div>
  {#if others && ownStructure}
    <div class="form-own"><span class="form-own-label">{t('chips.structure')}</span><span><IdsLabel text={(ownStructure.part ?? ownStructure).label} />{#if proposed(current, ownStructure.by)}<sup title={t('chips.derived')}>*</sup>{/if}</span></div>
  {/if}
  {#if me && !others && captioned}
    <button type="button" class="form-arrange" {disabled} aria-haspopup="dialog" onclick={() => arranging = true}>{t('structure.arrange')}</button>
  {/if}
  {#if arranging}
    <StructureDialog of={written} forms={arranged} known={structureOf} read={stands} current={chosen ?? current} close={() => arranging = false} changed={rearranged} />
  {/if}
  {#if linkedHere || canLink}
    <div class="form-link">
      {#if linkedHere}
        <span class="form-linked">✓ {t('form.link.linked', { char: written })}</span>
        {#if linkedByMe}<button type="button" class="form-unlink" disabled={disabled || linking} onclick={unlink}
          aria-label={t('form.link.remove', { char: written })} title={t('form.link.remove', { char: written })}>×</button>{/if}
      {:else}
        <button type="button" class="form-link-add" disabled={disabled || linking} aria-busy={linking} onclick={link}>
          {t('form.link.add', { char: written })}</button>
      {/if}
      {#if linkError}<span class="form-link-error" role="alert">{linkError}</span>{/if}
    </div>
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
  .form-add{flex:none;min-width:40px;padding:4px 10px;font-size:20px;line-height:1.2;color:var(--muted);border-style:dashed}
  .form-add[aria-expanded="true"]{color:var(--accent);border-color:var(--accent)}
  .form-picker{display:flex;flex-direction:column;gap:12px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
  .form-picker :global(.character-search){width:100%;min-width:0}
  .form-section{display:flex;flex-direction:column;gap:6px}
  .form-section-title{font-size:11px;color:var(--muted)}
  .form-options{display:flex;flex-wrap:wrap;gap:6px}
  .form-option{display:inline-flex;flex-direction:column;align-items:center;gap:2px;min-width:44px;padding:5px 9px;font-size:21px;line-height:1.2;
    font-family:var(--char-font)}
  .form-option small{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  .form-option:not(:disabled):hover{border-color:var(--accent)}
  .form-compose{align-self:flex-start;font-size:13px;padding:4px 10px}
  .form-note{margin:0;font-size:11px;color:var(--muted)}
  .form-link{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;line-height:1.4}
  .form-link-add,.form-arrange{border:0;background:transparent;color:var(--accent);padding:0;font:inherit;font-size:12px;text-align:left;
    text-decoration:underline;text-underline-offset:3px}
  .form-link-add:not(:disabled):hover,.form-arrange:not(:disabled):hover{color:var(--ink)}
  .form-arrange{align-self:flex-start;line-height:1.4}
  .form-linked{color:var(--muted)}
  .form-unlink{border:0;background:transparent;color:var(--muted);padding:0 2px;font-size:15px;line-height:1}
  .form-unlink:not(:disabled):hover{color:var(--wrong)}
  .form-link-error{color:var(--wrong)}
</style>
