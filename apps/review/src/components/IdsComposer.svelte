<script>
  // A written form typed or built as an ideographic description sequence. It starts from what the
  // reader typed, or else from the description of the crop's character (`char`), and hands the
  // sequence to `onuse` once it is whole.
  //
  // The sequence is held twice, kept in step: as text in the field, which also takes the ASCII
  // operator names (LR or lr for ⿰), and as a tree of boxes laid out as the operators place their
  // parts, where an empty slot shows (and is written) as ＋. A sequence with an empty slot is not
  // whole; ？ typed into a slot is the part no character names. One part of the tree is chosen at a time. An operator
  // put on a slot splits it, the slot's part first (⿰ on 失 gives ⿰失＋); put on an operator, it takes
  // that operator's place and keeps its parts. × takes one level away, keeping its first filled part.
  // A component found, typed into the part field or offered by the variant table replaces the chosen
  // part, and a component with a description of its own can be split into it (𠤕 into ⿱匕矢).
  // The part picker under the tree has three tabs: a component found by name or reading, the parts
  // palette (IdsParts), and the parts of any character or description typed (IdsFrom).
  // Every change can be undone, a hundred steps back.
  import { tick, untrack } from 'svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsFrom from './IdsFrom.svelte'
  import IdsParts from './IdsParts.svelte'
  import { CODES, KEYS, OPERATORS, arity, isDescription, isNode, isSequence, nextEmpty, parse, partAt, paths,
    replaceAt, samePath, swapOp, unalias, unwrap, clear, wrap, write, namingOperator } from '../lib/ids.js'
  import { lookupChar } from '../lib/decompose.js'
  import { structure } from '../lib/layers.js'
  import { t } from '../lib/i18n.svelte.js'
  let { start = '', char = '', disabled = false, onuse } = $props()

  // Each operator's name, the key that puts it, and its icon: the box it fills (the accent) and the
  // places of its parts inside it (dashed), each a line or a box as [left, top, right, bottom] in a
  // unit square; ⿻ is two boxes over each other, the first in the accent. 〾 marks its part as a
  // variant, drawn as a wave.
  const NAMES = {
    '⿰': 'leftRight', '⿱': 'aboveBelow', '⿲': 'leftMiddleRight', '⿳': 'aboveMiddleBelow', '⿴': 'surround',
    '⿵': 'fromAbove', '⿶': 'fromBelow', '⿷': 'fromLeft', '⿸': 'fromUpperLeft', '⿹': 'fromUpperRight',
    '⿺': 'fromLowerLeft', '⿻': 'overlaid', '⿼': 'fromRight', '⿽': 'fromLowerRight', '⿾': 'mirrored',
    '⿿': 'rotated', '㇯': 'subtracted', '〾': 'variant',
  }
  const KEY = Object.fromEntries(Object.entries(KEYS).map(([key, op]) => [op, key]))
  const ICON = {
    '⿰': [[.5, 0, .5, 1]], '⿱': [[0, .5, 1, .5]], '⿲': [[.33, 0, .33, 1], [.67, 0, .67, 1]], '⿳': [[0, .33, 1, .33], [0, .67, 1, .67]],
    '⿴': [[.25, .25, .75, .75]], '⿵': [[.25, .3, .75, 1]], '⿶': [[.25, 0, .75, .7]], '⿷': [[.3, .25, 1, .75]],
    '⿸': [[.35, .35, 1, 1]], '⿹': [[0, .35, .65, 1]], '⿺': [[.35, 0, 1, .65]], '⿻': [[.25, .25, 1, 1]],
    '⿼': [[0, .25, .7, .75]], '⿽': [[0, 0, .65, .65]], '⿾': [[.5, .1, .5, .9]], '⿿': [[.2, .2, .8, .8]],
    '㇯': [[.3, .3, .7, .7]], '〾': [],
  }
  const OUTER = { '⿻': [0, 0, .75, .75] }
  const name = op => t(`form.ids.op.${NAMES[op]}`)
  // How the box tree lays a node's parts out: in a row, in a column, the second inside the first, or
  // one over the other.
  const LAYOUT = { '⿰': 'row', '⿲': 'row', '⿱': 'column', '⿳': 'column', '⿻': 'stack', '㇯': 'stack', '⿾': 'stack', '⿿': 'stack', '〾': 'stack' }
  const layout = op => LAYOUT[op] ?? 'enclosure'
  const HISTORY = 100

  let tree = $state(null), chosen = $state(null), draft = $state(''), piece = $state(''), note = $state('')
  let field = $state(null), slot = $state(null), root = $state(null), query = $state('')
  let past = $state.raw([]), future = $state.raw([]), typing = false, run = false
  // What the structure route answered for each character met so far (its descriptions and regional
  // shapes, or that it is loading or failed), and what the variant table says of each component in
  // them. Each character is asked once, again only after a failure.
  let swaps = $state({}), known = $state({})
  const asking = new Map()
  function read(c, { again = false } = {}) {
    if (!c) return Promise.resolve(null)
    if (asking.has(c) && !(again && known[c]?.status === 'failed')) return asking.get(c)
    known = { ...known, [c]: { status: 'loading' } }
    const asked = structure(c).then(found => {
      swaps = { ...swaps, ...found.substitutes }
      known = { ...known, [c]: { status: 'ready', sequences: found.sequences ?? [], shapes: found.shapes ?? [] } }
      return known[c]
    }, () => { known = { ...known, [c]: { status: 'failed' } }; return known[c] })
    asking.set(c, asked)
    return asked
  }
  const sequencesOf = c => known[c]?.sequences ?? []

  // Each crop starts again: from a description the reader had typed into the search, or else from
  // the character's own. What is typed into the search afterwards leaves the draft alone.
  $effect(() => {
    const of = char
    untrack(() => {
      const typed = isSequence(start) ? start : ''
      past = []; future = []; note = ''
      reset(typed)
      if (!typed && of) read(of).then(found => { if (!draft && found?.sequences?.length) reset(found.sequences[0]) })
    })
  })
  function reset(text) {
    draft = text; chosen = null; piece = ''
    tree = parse(text, { open: true }).tree ?? null
  }

  const whole = $derived(write(tree))
  // What is wrong with the text as typed, once there is some; a name half typed (the L of LR) is
  // asked to be finished.
  const typed = $derived(parse(draft, { open: true }))
  const problem = $derived(note || (!draft || !typed.problem ? '' : namingOperator(draft) ? t('form.ids.problem.naming')
    : t(`form.ids.problem.${typed.problem}`)))
  // The first slot still empty, named by its place: a sequence is whole only once there is none.
  const gap = $derived(isNode(tree) ? nextEmpty(tree) : null)
  const pending = $derived(gap ? t('form.ids.problem.empty', { n: gap.at(-1) + 1, name: name(partAt(tree, gap.slice(0, -1)).op) }) : '')
  const usable = $derived(isNode(tree) && !gap && !typed.problem && isDescription(whole))
  const picked = $derived(chosen ? partAt(tree, chosen) : undefined)
  const text = part => part === null || part === undefined ? '' : write(part)

  // A change to the tree, kept for undoing, a run of typing in the field as one; the field shows it
  // unless the reader is typing there.
  function change(next, select) {
    if (!(typing && run)) past = [...past.slice(1 - HISTORY), { tree: $state.snapshot(tree), chosen }]
    run = typing
    future = []
    commit(next, select)
  }
  function commit(next, select) {
    tree = next; chosen = select; note = ''
    if (!typing) draft = write(next)
    const part = select ? partAt(next, select) : undefined
    piece = text(part)
    if (typeof part === 'string') read(lookupChar(part))
  }
  // One step back or forward: the state left is kept on the other side.
  function step(from, to) {
    const last = from.at(-1)
    to.push({ tree: $state.snapshot(tree), chosen })
    commit(last.tree, last.chosen)
    return from.slice(0, -1)
  }
  function undo() { run = false; if (past.length) { const to = [...future]; past = step(past, to); future = to.slice(-HISTORY) } }
  function redo() { run = false; if (future.length) { const to = [...past]; future = step(future, to); past = to.slice(-HISTORY) } }

  // The field, read as it is typed: ASCII operator names become operators where the caret is.
  function edit(event) {
    if (event?.isComposing) return
    const typedText = field.value, caret = field.selectionStart ?? typedText.length
    const written = unalias(typedText)
    if (written !== typedText) {
      const at = [...unalias(typedText.slice(0, caret))].length
      field.value = written
      const offset = [...written].slice(0, at).join('').length
      field.setSelectionRange(offset, offset)
    }
    draft = written
    const found = parse(written, { open: true })
    if (found.problem) return
    typing = true
    change(found.tree, null)
    typing = false
  }

  // Where an operator or a part goes when nothing is chosen: the first empty slot.
  const target = () => chosen ?? (tree === null ? [] : nextEmpty(tree))
  function operator(op) {
    if (disabled) return
    const at = target()
    // Every slot filled and none chosen: the whole sequence becomes the operator's first part.
    if (!at) { change(wrap(op, tree), arity(op) > 1 ? [1] : []); return }
    const part = partAt(tree, at)
    if (isNode(part)) {
      const swapped = swapOp(tree, at, op)
      if (swapped.problem) note = t(`form.ids.problem.${swapped.problem}`, { name: name(op) })
      else change(swapped.tree, at)
      return
    }
    // The slot's part stays first; the slot to fill next is the new one beside it, or the operator's
    // only one when it was empty.
    const select = part === null ? [...at, 0] : arity(op) === 1 ? at : [...at, 1]
    change(replaceAt(tree, at, wrap(op, part)), select)
  }
  function place(part) {
    const at = target()
    if (!at) { note = t('form.ids.chooseFirst'); return }
    const next = replaceAt(tree, at, part)
    change(next, nextEmpty(next, at) ?? at)
  }
  function component(item) { query = ''; if (!disabled) place(item.char) }
  function swap(item) { place(parse(item.char).tree ?? item.char) }
  function split(sequence) { const found = parse(sequence, { open: true }); if (!found.problem) place(found.tree) }
  // A part or a node taken from the parts palette or the "Take parts from" tree, as written.
  function take(written) { const found = parse(written, { open: true }); if (!disabled && !found.problem && found.tree !== null) place(found.tree) }
  // The descriptions a component can be split into, those the editor can read.
  const splits = $derived(typeof picked === 'string' ? sequencesOf(picked).filter(sequence => !parse(sequence, { open: true }).problem) : [])
  function choose(path) {
    chosen = samePath(chosen, path) ? null : path
    note = ''
    const part = chosen ? partAt(tree, chosen) : undefined
    piece = text(part)
    if (typeof part === 'string') read(lookupChar(part))
  }
  function remove(path) { if (!disabled && isNode(partAt(tree, path))) change(unwrap(tree, path), path) }
  function empty(path) { if (!disabled && path && partAt(tree, path) !== null) change(clear(tree, path), path) }

  // The part field: Enter puts what it holds in the chosen slot and moves on to the next empty one.
  function enter() {
    const typedText = unalias(piece.trim())
    if (!typedText) return
    const found = parse(typedText, { open: true })
    if (found.problem) { note = t(`form.ids.problem.${found.problem}`); return }
    place(found.tree)
    queueMicrotask(() => slot?.select())
  }

  const pathKey = path => path.join('.')
  async function focusChosen() {
    await tick()
    if (chosen) root?.querySelector(`[data-path="${pathKey(chosen)}"]`)?.focus()
  }
  // Keys while the editor has the focus outside a text field: an operator for each key of the number
  // row and the row under it, by where it sits so any layout has them, Tab through the parts,
  // Backspace to empty one, Ctrl+Z to undo. A key the editor takes is its own: it is marked taken, so
  // the page's own shortcuts (a crop's review letters, the forms page's undo) leave it alone.
  function keydown(event) {
    if (event.isComposing || event.key === 'Process' || disabled) return
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return
    // Undo and redo work from anywhere in the editor; the focus stays in a picker that has it.
    const command = event.ctrlKey || event.metaKey, inPicker = Boolean(event.target.closest?.('.ids-pick-tabs, .ids-pick-panel'))
    const after = () => { if (!inPicker) focusChosen() }
    if (command && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) redo(); else undo(); after(); return }
    if (command && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); after(); return }
    // Any other key a picker handles, or pressed inside one (its tabs, its grid, its tree), is the picker's own.
    if (event.defaultPrevented || inPicker) return
    if (command || event.altKey) return
    const op = event.shiftKey ? null : CODES[event.code] ?? KEYS[event.key]
    if (op) { event.preventDefault(); operator(op); focusChosen(); return }
    if (event.key === 'Tab' && event.target.closest('.ids-tree') && tree !== null) {
      // From the part that has the focus, else the chosen one.
      const from = event.target.closest('[data-path]')?.dataset.path
      const all = paths(tree), at = all.findIndex(path => from !== undefined ? pathKey(path) === from : samePath(path, chosen))
      const next = at + (event.shiftKey ? -1 : 1)
      if (next < 0 || next >= all.length) return
      event.preventDefault()
      choose(all[next])
      focusChosen()
      return
    }
    if (event.key === 'Backspace' || event.key === 'Delete') { event.preventDefault(); empty(target()); focusChosen(); return }
    // Any other character typed goes to the part field.
    if (event.key.length === 1 && event.key !== ' ') { event.preventDefault(); slot?.focus(); piece = event.key; tick().then(() => slot?.setSelectionRange(piece.length, piece.length)) }
  }
  function use() { if (usable && !disabled) onuse(whole) }

  // The part picker's tabs: a component found by name or reading, the parts palette, and the parts of
  // any character or description typed. Arrow keys, Home and End move between them.
  const uid = $props.id()
  const PICKERS = ['find', 'parts', 'from']
  let picker = $state('find'), opened = $state(new Set(['find'])), pickButtons = $state([])
  function open(tab) { picker = tab; if (!opened.has(tab)) opened = new Set([...opened, tab]) }
  function pickKey(event, index) {
    const last = PICKERS.length - 1
    const to = { ArrowRight: index === last ? 0 : index + 1, ArrowLeft: index === 0 ? last : index - 1, Home: 0, End: last }[event.key]
    if (to === undefined) return
    event.preventDefault()
    open(PICKERS[to])
    pickButtons[to]?.focus()
  }
</script>

{#snippet icon(op, size)}
  {@const [x0, y0, x1, y1] = OUTER[op] ?? [0, 0, 1, 1]}
  <svg viewBox="-1.5 -1.5 23 23" width={size} height={size} aria-hidden="true" class="ids-icon">
    {#each ICON[op] as [a, b, c, d], i (i)}
      {#if a === c || b === d}<line class="inner" x1={a * 20} y1={b * 20} x2={c * 20} y2={d * 20} />
      {:else}<rect class="inner" x={a * 20} y={b * 20} width={(c - a) * 20} height={(d - b) * 20} />{/if}
    {/each}
    {#if op === '〾'}<path class="inner" d="M3 10q2.5-4 5 0t5 0 5 0" />{/if}
    <rect x={x0 * 20} y={y0 * 20} width={(x1 - x0) * 20} height={(y1 - y0) * 20} />
  </svg>
{/snippet}

{#snippet box(part, path, parent)}
  {@const selected = samePath(chosen, path)}
  {#if isNode(part)}
    {@const what = parent ? t('form.ids.part', { part: name(part.op), n: parent.n, name: name(parent.op) }) : name(part.op)}
    <div class="ids-node {layout(part.op)}" class:chosen={selected} data-op={part.op}>
      <div class="ids-head">
        <button type="button" class="ids-op" data-path={pathKey(path)} aria-pressed={selected} {disabled}
          aria-label={t('form.ids.node', { name: name(part.op), sequence: write(part) })} title={`${part.op} ${name(part.op)}`}
          onclick={() => choose(path)}>{@render icon(part.op, 14)}</button>
        <button type="button" class="ids-unwrap" tabindex="-1" {disabled} aria-label={t('form.ids.unwrapNode', { what })} title={t('form.ids.unwrap')}
          onclick={() => remove(path)}>×</button>
      </div>
      <div class="ids-parts">
        {#each part.parts as inner, i (pathKey([...path, i]))}
          <div class="ids-cell" data-index={i}>{@render box(inner, [...path, i], { op: part.op, n: i + 1 })}</div>
        {/each}
      </div>
    </div>
  {:else}
    {@const label = part === null ? t('form.ids.empty') : part}
    <button type="button" class="ids-part" class:empty={part === null} class:chosen={selected} aria-pressed={selected}
      data-path={pathKey(path)} {disabled} title={part === null ? label : t('form.ids.choose', { part })}
      aria-label={parent ? t('form.ids.part', { part: label, n: parent.n, name: name(parent.op) }) : label}
      onclick={() => choose(path)}>{part ?? '＋'}</button>
  {/if}
{/snippet}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ids-composer" bind:this={root} onkeydown={keydown}>
  <label class="ids-field">
    <span class="ids-title">{t('form.ids.label')}</span>
    <input bind:this={field} value={draft} {disabled} lang="ja" spellcheck="false" autocomplete="off"
      placeholder="⿰⿱匕失⿱コ疋" aria-invalid={Boolean(problem)} oninput={edit} oncompositionend={() => edit()} onblur={() => { run = false; if (!typed.problem && tree !== null) draft = write(tree) }}
      onkeydown={event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); use() } }} />
  </label>
  <div class="ids-operators" role="group" aria-label={t('form.ids.operators')}>
    {#each OPERATORS as [op] (op)}
      <button type="button" class="ids-key" data-op={op} {disabled} aria-label={name(op)}
        title={t('form.ids.operatorTitle', { op, name: name(op), key: KEY[op] })} onclick={() => operator(op)}>{@render icon(op, 22)}</button>
    {/each}
  </div>
  <div class="ids-tree" role="group" aria-label={t('form.ids.structure')}>
    {@render box(tree, [], null)}
  </div>
  <div class="ids-tools">
    <label class="ids-slot">
      <span class="ids-title">{t('form.ids.slot')}</span>
      <input bind:this={slot} bind:value={piece} {disabled} lang="ja" spellcheck="false" autocomplete="off"
        placeholder={t('form.ids.slotHint')} onkeydown={event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); enter() } }} />
    </label>
    <button type="button" class="ids-clear" disabled={disabled || !chosen || picked === null} onclick={() => empty(chosen)}>{t('form.ids.clear')}</button>
    <button type="button" class="ids-remove" disabled={disabled || !isNode(picked)} onclick={() => remove(chosen)}>{t('form.ids.unwrap')}</button>
    <button type="button" class="ids-undo" disabled={disabled || !past.length} title={t('forms.undo.title')} onclick={undo}>{t('forms.undo')}</button>
    <button type="button" class="ids-redo" disabled={disabled || !future.length} title={t('forms.redo.title')} onclick={redo}>{t('forms.redo')}</button>
  </div>
  {#if problem}<p class="ids-problem" role="status">{problem}</p>
  {:else if pending}<p class="ids-pending" role="status">{pending}</p>{/if}
  {#if typeof picked === 'string' && ((swaps[picked] ?? []).length || splits.length)}
    <div class="ids-swaps" role="group" aria-label={t('form.ids.swaps', { part: picked })}>
      <span class="ids-title">{t('form.ids.swaps', { part: picked })}</span>
      <div class="ids-options">
        {#each swaps[picked] ?? [] as item (item.char)}
          <button type="button" class="ids-swap" {disabled} title={`${picked} → ${item.char} · ${item.count}`} onclick={() => swap(item)}>{item.char}</button>
        {/each}
        {#each splits as sequence (sequence)}
          <button type="button" class="ids-swap ids-split" {disabled} title={t('form.ids.split', { part: picked })} onclick={() => split(sequence)}>{sequence}</button>
        {/each}
      </div>
    </div>
  {/if}
  <div class="ids-search">
    <span class="ids-title">{chosen && picked !== null ? t('form.ids.replace', { part: write(picked) }) : t('form.ids.insert')}</span>
    <div class="ids-pick-tabs" role="tablist" aria-label={t('form.ids.pick.label')}>
      {#each PICKERS as tab, index (tab)}
        <button type="button" role="tab" id={`${uid}-pick-${tab}`} aria-controls={opened.has(tab) ? `${uid}-pick-${tab}-panel` : undefined} bind:this={pickButtons[index]}
          class:active={picker === tab} aria-selected={picker === tab} tabindex={picker === tab ? 0 : -1}
          onclick={() => open(tab)} onkeydown={event => pickKey(event, index)}>{t(`form.ids.pick.${tab}`)}</button>
      {/each}
    </div>
    <!-- A picker once opened stays, hidden while another is shown, so what was typed in it is kept. -->
    {#each PICKERS as tab (tab)}
      {#if opened.has(tab)}
        <div class="ids-pick-panel" role="tabpanel" id={`${uid}-pick-${tab}-panel`} aria-labelledby={`${uid}-pick-${tab}`} hidden={picker !== tab}>
          {#if tab === 'find'}<CharacterSearch compact codePoints bind:value={query} label={t('form.ids.search')} placeholder={t('search.placeholder')} onselect={component} />
          {:else if tab === 'parts'}<IdsParts {disabled} onpick={take} />
          {:else}<IdsFrom {known} {swaps} lookup={read} {disabled} ontake={take} />{/if}
        </div>
      {/if}
    {/each}
  </div>
  <div class="ids-actions">
    <p class="ids-keys">{t('form.ids.keys')}</p>
    <button type="button" class="ids-use" disabled={disabled || !usable} onclick={use}>{t('form.ids.use')}</button>
  </div>
</div>

<style>
  .ids-composer{--ids-font:"GenZui Sans","Noto Serif Tangut","Klee One","LXGW WenKai TC",serif;display:flex;flex-direction:column;gap:10px}
  .ids-field,.ids-slot{display:flex;flex-direction:column;gap:4px}
  .ids-title{font-size:11px;color:var(--muted)}
  .ids-field input{font-size:20px;padding:6px 8px;font-family:var(--ids-font)}
  .ids-field input[aria-invalid="true"]{border-color:var(--fault)}
  .ids-operators{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:4px}
  @media (max-width:520px){.ids-operators{grid-template-columns:repeat(6,minmax(0,1fr))}}
  .ids-key{display:grid;place-items:center;padding:5px 0;min-width:0}
  .ids-key:not(:disabled):hover,.ids-key:focus-visible{border-color:var(--accent)}
  .ids-icon{display:block;overflow:visible}
  .ids-icon :is(rect,line,path){fill:none;stroke:var(--accent);stroke-width:1.6}
  .ids-icon .inner{stroke:var(--ink-soft);stroke-dasharray:2 1.5}

  .ids-tree{display:grid;justify-items:center;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface-subtle);min-height:72px}
  .ids-node{position:relative;display:flex;flex-direction:column;min-width:0;padding:22px 6px 6px;
    border:1.5px dashed var(--line-strong);border-radius:8px}
  .ids-tree > .ids-node,.ids-tree > .ids-part{width:min(100%,360px)}
  .ids-node.chosen{border-style:solid;border-color:var(--accent);background:var(--accent-light)}
  .ids-head{position:absolute;top:2px;left:4px;right:4px;display:flex;justify-content:space-between;align-items:center;pointer-events:none}
  .ids-head button{pointer-events:auto}
  .ids-op{display:grid;place-items:center;padding:1px 3px;border-color:transparent;background:none}
  .ids-op:hover,.ids-op[aria-pressed="true"]{border-color:var(--accent)}
  .ids-unwrap{padding:0 5px;font-size:13px;line-height:1.2;color:var(--muted);border-color:transparent;background:none}
  .ids-unwrap:not(:disabled):hover{color:var(--wrong);border-color:var(--wrong)}
  .ids-parts{flex:1;display:flex;gap:6px;min-width:0}
  .ids-cell{flex:1;display:grid;min-width:0}
  .row > .ids-parts{flex-direction:row}
  .column > .ids-parts,.stack > .ids-parts{flex-direction:column}
  /* An enclosure: the first part fills the box, its glyph on the closed side, and the second sits
     inside it where the operator puts it, leaving a band of the first open around it. */
  .enclosure > .ids-parts{display:grid;gap:0;grid-template-columns:42px minmax(min-content,1fr) 42px;grid-template-rows:42px minmax(min-content,1fr) 42px}
  .enclosure > .ids-parts > .ids-cell[data-index="0"]{grid-area:1/1/4/4}
  .enclosure > .ids-parts > .ids-cell[data-index="1"]{z-index:1;margin:4px}
  .enclosure > .ids-parts > .ids-cell[data-index="1"] > .ids-part{background:var(--surface)}
  .enclosure > .ids-parts > .ids-cell[data-index="0"] > .ids-part{font-size:24px;padding:2px 8px}
  [data-op="⿴"] > .ids-parts > [data-index="1"]{grid-area:2/2/3/3}
  [data-op="⿵"] > .ids-parts > [data-index="1"]{grid-area:2/2/4/3}
  [data-op="⿶"] > .ids-parts > [data-index="1"]{grid-area:1/2/3/3}
  [data-op="⿷"] > .ids-parts > [data-index="1"]{grid-area:2/2/3/4}
  [data-op="⿸"] > .ids-parts > [data-index="1"]{grid-area:2/2/4/4}
  [data-op="⿹"] > .ids-parts > [data-index="1"]{grid-area:2/1/4/3}
  [data-op="⿺"] > .ids-parts > [data-index="1"]{grid-area:1/2/3/4}
  [data-op="⿼"] > .ids-parts > [data-index="1"]{grid-area:2/1/3/3}
  [data-op="⿽"] > .ids-parts > [data-index="1"]{grid-area:1/1/3/3}
  [data-op="⿴"] > .ids-parts > [data-index="0"] > .ids-part,
  [data-op="⿸"] > .ids-parts > [data-index="0"] > .ids-part{place-items:start start}
  [data-op="⿵"] > .ids-parts > [data-index="0"] > .ids-part{place-items:start center}
  [data-op="⿶"] > .ids-parts > [data-index="0"] > .ids-part{place-items:end center}
  [data-op="⿷"] > .ids-parts > [data-index="0"] > .ids-part{place-items:center start}
  [data-op="⿼"] > .ids-parts > [data-index="0"] > .ids-part{place-items:center end}
  [data-op="⿹"] > .ids-parts > [data-index="0"] > .ids-part{place-items:start end}
  [data-op="⿺"] > .ids-parts > [data-index="0"] > .ids-part{place-items:end start}
  [data-op="⿽"] > .ids-parts > [data-index="0"] > .ids-part{place-items:end end}

  .ids-part{display:grid;place-items:center;min-width:44px;min-height:48px;padding:2px 6px;font-size:28px;line-height:1.1;
    font-family:var(--ids-font);color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px}
  .ids-part:not(:disabled):hover{border-color:var(--line-strong)}
  .ids-part.empty{font-size:18px;color:var(--muted);border-style:dashed;border-color:var(--line-strong);background:transparent}
  .ids-part.chosen{border:2px solid var(--accent);padding:1px 5px;background:var(--accent-light)}
  .ids-op:focus-visible,.ids-part:focus-visible{outline:2px solid var(--focus-ring);outline-offset:1px}

  .ids-tools{display:flex;flex-wrap:wrap;align-items:flex-end;gap:6px}
  .ids-slot{flex:1 1 180px;min-width:0}
  .ids-slot input{font-size:14px;padding:7px 8px;min-height:38px}
  .ids-slot input:not(:placeholder-shown){font-size:18px;padding:5px 8px;font-family:var(--ids-font)}
  .ids-tools > button{padding:5px 10px;font-size:13px}
  .ids-swaps{display:flex;flex-direction:column;gap:4px}
  .ids-options{display:flex;flex-wrap:wrap;gap:6px}
  .ids-swap{min-width:40px;padding:3px 8px;font-size:20px;line-height:1.2;font-family:var(--ids-font)}
  .ids-split{font-size:16px;color:var(--muted)}
  .ids-swap:not(:disabled):hover{border-color:var(--accent)}
  .ids-problem{margin:0;font-size:12px;color:var(--fault)}
  .ids-pending{margin:0;font-size:12px;color:var(--muted)}
  .ids-search{display:flex;flex-direction:column;gap:4px}
  .ids-search :global(.character-search){width:100%;min-width:0}
  .ids-pick-tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--line);padding-bottom:4px}
  .ids-pick-tabs button{border:0;background:transparent;font-size:12px;padding:6px 10px;border-radius:5px;color:var(--muted)}
  .ids-pick-tabs button.active{background:var(--surface-selected);color:var(--ink)}
  .ids-pick-panel{min-width:0}
  .ids-pick-panel[hidden]{display:none}
  .ids-actions{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
  .ids-keys{margin:0;font-size:11px;line-height:1.5;color:var(--muted)}
  .ids-use{flex:none;padding:5px 14px}
</style>
