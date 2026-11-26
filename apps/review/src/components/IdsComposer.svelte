<script>
  // A written form typed or built as an ideographic description sequence. It starts from what the
  // reader typed, or else from the description of the crop's character (`char`), and hands the
  // sequence to `onuse` once it is whole.
  //
  // The sequence is held twice, kept in step: as text in the field, which also takes the ASCII
  // operator names (LR or lr for ⿰), and as a tree of boxes laid out as the operators place their
  // parts, where an empty slot shows (and is written) as ⬚. Selecting text in the sequence field
  // selects that part in the tree so clicking an operator or component acts on it. Parts can be
  // reordered within a node or swapped across branches by dragging (mouse or touch), lifting with
  // Space, or pressing Alt+Arrow; right-click, Menu or Shift+F10 opens a part's context menu.
  // Every change can be undone, a hundred steps back.
  import { tick, untrack } from 'svelte'
  import { rateFormsFor } from 'han-compose/rate-forms'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsPreview from './IdsPreview.svelte'
  import IdsFrom from './IdsFrom.svelte'
  import IdsParts from './IdsParts.svelte'
  import FigureText from './FigureText.svelte'
  import OperatorIcon from './OperatorIcon.svelte'
  import { CODES, KEYS, OPERATORS, OP_NAMES, arity, isDescription, isNode, isSequence, layout, neighbor, nextEmpty, parse, partAt, paths,
    rangeToken, reorderSiblings, replaceAt, samePath, siblings, swapOp, swapParts, swappable, tokenize, unalias, unwrap, clear, wrap, write,
    namingOperator, isComponent, leaves, EMPTY } from '../lib/ids.js'
  import { lookupChar, regionNames, startCharacter, variants } from '../lib/decompose.js'
  import { ids } from '../lib/layers.js'
  import { figuresFor } from '../lib/partFigures.js'
  import { base, t } from '../lib/i18n.svelte.js'
  let { start = '', char = '', disabled = false, onuse } = $props()

  // The key that puts each operator, and its name.
  const KEY = Object.fromEntries(Object.entries(KEYS).map(([key, op]) => [op, key]))
  const name = op => t(`form.ids.op.${OP_NAMES[op]}`)
  const HISTORY = 100
  const PART_DRAG = 'application/x-ids-part'
  // Where the preview's drawings come from and the terms they carry.
  const SOURCES = 'https://github.com/mkpoli/glyph-atlas/blob/main/docs/licensing.md#composer-drawings'

  let tree = $state(null), chosen = $state(null), lone = $state(false), draft = $state(''), piece = $state(''), note = $state('')
  let field = $state(null), slot = $state(null), root = $state(null), treeEl = $state(null), query = $state('')
  let past = $state.raw([]), future = $state.raw([]), typing = false, run = false
  // What the IDS route answered for each character met so far (its descriptions and regional
  // shapes, or that it is loading or failed), and what the variant table says of each component in
  // them. Each character is asked once, again only after a failure.
  let swaps = $state({}), known = $state({})
  const asking = new Map()
  function read(c, { again = false } = {}) {
    if (!c) return Promise.resolve(null)
    if (asking.has(c) && !(again && known[c]?.status === 'failed')) return asking.get(c)
    known = { ...known, [c]: { status: 'loading' } }
    const asked = ids(c).then(found => {
      swaps = { ...swaps, ...found.substitutes }
      known = { ...known, [c]: { status: 'ready', sequences: found.sequences ?? [], shapes: found.shapes ?? [] } }
      return known[c]
    }, () => { known = { ...known, [c]: { status: 'failed' } }; return known[c] })
    asking.set(c, asked)
    return asked
  }
  const sequencesOf = c => known[c]?.sequences ?? []
  // The registered figures of each component met, asked with its descriptions; a failed read is asked again.
  let figures = $state({})
  const figuring = new Set()
  function readFigures(c) {
    if (!isComponent(c) || figuring.has(c)) return
    figuring.add(c)
    figuresFor(c).then(found => { figures = { ...figures, [c]: found } }, () => figuring.delete(c))
  }

  // Each crop starts again: from a description or single character the reader had typed into the
  // search, or else from the character's own. What is typed into the search afterwards leaves the
  // draft alone. Any existing character can also be loaded as the starting draft, with its regional
  // or variant decompositions offered to pick from.
  let source = $state(''), startQuery = $state(''), startPicked = $state(false), startSearch = $state(null), startSeq = 0, draftRev = 0
  $effect(() => {
    const of = char
    untrack(() => {
      const typed = isSequence(start) || rateFormsFor(start).length ? start
        : !start && rateFormsFor(of).length ? of : ''
      const initial = !typed ? (startCharacter(start) || startCharacter(of)) : ''
      past = []; future = []; note = ''
      source = initial; startQuery = ''; startPicked = false
      const seq = ++startSeq
      reset(typed)
      const rev = draftRev
      if (!typed && initial) {
        read(initial).then(found => {
          if (seq !== startSeq || rev !== draftRev || draft) return
          const first = found?.sequences?.[0] ? parse(found.sequences[0], { open: true }) : null
          const next = first && !first.problem && first.tree ? found.sequences[0] : variants(found).find(v => v.tree)?.text
          if (next) reset(next)
        })
      }
    })
  })
  const startCandidate = $derived(startCharacter(startQuery))
  const startEntry = $derived(source ? known[source] : undefined)
  const startVariants = $derived(variants(startEntry).filter(v => v.tree))
  const startStatus = $derived(source ? startEntry?.status ?? null : null)
  async function loadChar(raw, { again = false } = {}) {
    if (disabled) return
    const c = startCharacter(raw)
    if (!c) return
    startSearch?.close()
    source = c
    startQuery = c
    startPicked = true
    const seq = ++startSeq
    const token = raw.trim()
    const reviewed = rateFormsFor(token)
    if (!again && reviewed.length && (whole !== token || chosen !== null)) change(parse(token, { open: true }).tree, null)
    const rev = draftRev
    const found = await read(c, { again })
    if (seq !== startSeq || rev !== draftRev || disabled || reviewed.length) return
    const list = variants(found).filter(v => v.tree)
    if (list.length && (whole !== list[0].text || chosen !== null)) change(list[0].tree, null)
  }
  function reset(text) {
    draftRev += 1
    draft = text; chosen = null; lone = false; piece = ''
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
  // A whole draft holding a letter (⿸广⿰KO) is drawn but cannot be saved as a form.
  const pending = $derived(gap ? t('form.ids.problem.empty', { n: gap.at(-1) + 1, name: name(partAt(tree, gap.slice(0, -1)).op) })
    : leaves(tree).some(leaf => !isComponent(leaf.char)) ? t('form.ids.problem.component') : '')
  const rootForms = $derived(rateFormsFor(whole))
  const usable = $derived(!gap && !typed.problem && (isDescription(whole) || rootForms.some(form => form.text === whole)))
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
    draftRev += 1
    tree = next; chosen = select; lone = false; note = ''
    if (!typing) draft = write(next)
    const part = select ? partAt(next, select) : undefined
    piece = text(part)
    if (typeof part === 'string') { read(lookupChar(part)); readFigures(lookupChar(part)) }
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
  // Sync a non-empty text selection in the sequence field with the tree's chosen part so clicking an
  // operator, component, swap, Clear or Unwrap acts on what was selected in the field.
  function selectField() {
    if (!field || typed.problem || tree === null) return
    const start = field.selectionStart ?? 0, end = field.selectionEnd ?? 0
    if (start === end) return
    const token = rangeToken(tokenize(draft), start, end)
    if (!token) return
    const node = partAt(tree, token.path)
    if (node === undefined) return
    chosen = token.path
    lone = isNode(node) && token.start === start && (end === token.tokenEnd || end === start + node.op.length)
    note = ''
    piece = text(node)
    if (typeof node === 'string') { read(lookupChar(node)); readFigures(lookupChar(node)) }
  }

  // Where an operator or a part goes when nothing is chosen: the first empty slot.
  const target = () => chosen ?? (tree === null ? [] : nextEmpty(tree))
  const draftBlocked = () => { if (typed.problem && draft) { note = t('form.ids.problem.draft'); return true } return false }
  function operator(op, explicitAt = undefined) {
    if (disabled || draftBlocked()) return
    const replace = explicitAt === undefined && lone
    const at = explicitAt === undefined ? target() : explicitAt
    // Every slot filled and none chosen: the whole sequence becomes the operator's first part.
    if (!at) { change(wrap(op, tree), arity(op) > 1 ? [1] : []); return }
    const part = partAt(tree, at)
    if (isNode(part)) {
      const swapped = swapOp(tree, at, op, { drop: replace })
      if (swapped.problem) note = t(`form.ids.problem.${swapped.problem}`, { name: name(op) })
      else { change(swapped.tree, at); if (replace) lone = true }
      return
    }
    // The slot's part stays first; the slot to fill next is the new one beside it, or the operator's
    // only one when it was empty.
    const select = part === null ? [...at, 0] : arity(op) === 1 ? at : [...at, 1]
    change(replaceAt(tree, at, wrap(op, part)), select)
  }
  function place(part, explicitAt = undefined) {
    if (draftBlocked()) return
    const at = explicitAt === undefined ? target() : explicitAt
    if (!at) { note = t('form.ids.chooseFirst'); return }
    const next = replaceAt(tree, at, part)
    change(next, nextEmpty(next, at) ?? at)
  }
  function component(item) { query = ''; if (!disabled) place(item.char) }
  function swap(item) { place(parse(item.char).tree ?? item.char) }
  function split(sequence) { const found = parse(sequence, { open: true }); if (!found.problem) place(found.tree) }
  // A part or a node taken from the parts palette, "Take parts from", or a text drop.
  function take(written, explicitAt = undefined) {
    if (disabled || draftBlocked()) return
    const found = parse(unalias(written), { open: true })
    if (!found.problem && found.tree !== null) place(found.tree, explicitAt)
  }
  function reorder(from, to) {
    if (disabled || draftBlocked()) return
    const next = reorderSiblings(tree, from, to)
    if (next !== tree) change(next, to)
  }
  function exchange(from, to) {
    if (disabled || draftBlocked()) return
    const next = swapParts(tree, from, to)
    if (next !== tree) change(next, to)
  }
  // The descriptions a component can be split into, those the editor can read.
  const splits = $derived(typeof picked === 'string' ? sequencesOf(lookupChar(picked)).filter(sequence => !parse(sequence, { open: true }).problem) : [])
  const rateToken = $derived(chosen === null ? whole : text(picked))
  const rateForms = $derived(chosen === null ? rootForms : rateFormsFor(rateToken))
  // The picked part as its other figures, the bare character among them once it carries a selector.
  const figured = $derived.by(() => {
    if (typeof picked !== 'string') return []
    const base = lookupChar(picked), found = figures[base] ?? []
    return [...(picked !== base && found.length ? [{ char: base, mj: null }] : []), ...found.filter(figure => figure.char !== picked && !rateForms.some(form => form.text === figure.char))]
  })
  let rateDrawing = $state(null)
  $effect(() => {
    if (rateForms.length && !rateDrawing) {
      import('han-compose/rate-drawing').then(module => { rateDrawing = module.rateDrawing }, () => {})
    }
  })
  function selectRateForm(form) {
    if (disabled || draftBlocked() || form.text === rateToken) return
    const next = parse(form.text, { open: true })
    if (next.problem || next.tree === null) return
    const path = chosen ?? []
    change(replaceAt(tree, path, next.tree), path)
  }
  const point = text => [...text].map(c => `U+${c.codePointAt(0).toString(16).toUpperCase()}`).join(' ')
  function choose(path, { toggle = true } = {}) {
    chosen = path && toggle && samePath(chosen, path) ? null : path
    lone = false
    note = ''
    const part = chosen ? partAt(tree, chosen) : undefined
    piece = text(part)
    if (typeof part === 'string') { read(lookupChar(part)); readFigures(lookupChar(part)) }
  }
  function remove(path) { if (!disabled && !draftBlocked() && path && isNode(partAt(tree, path))) change(unwrap(tree, path), path) }
  function empty(path) { if (!disabled && !draftBlocked() && path && partAt(tree, path) !== null) change(clear(tree, path), path) }

  // The part field: Apply or Enter puts what it holds in the chosen slot and moves on to the next empty one.
  const pieceDirty = $derived(Boolean(piece.trim()) && (chosen === null || unalias(piece.trim()) !== text(picked)))
  function enter() {
    if (disabled) return
    const typedText = unalias(piece.trim())
    if (!typedText) return
    const found = parse(typedText, { open: true })
    if (found.problem) { note = t(`form.ids.problem.${found.problem}`); return }
    place(found.tree)
    queueMicrotask(() => slot?.select())
  }

  const pathKey = path => path.join('.')
  const parsePath = written => written === '' ? [] : written.split('.').map(Number)
  async function focusChosen() {
    await tick()
    if (chosen) root?.querySelector(`[data-path="${pathKey(chosen)}"]`)?.focus()
  }

  // Drag-and-drop reorder (siblings) and swap (across branches), plus touch drag and Space carry.
  let overPath = $state(null), overSwap = $state(false)
  let dragging = $state.raw(null), carry = $state.raw(null)
  const overKind = path => overPath !== pathKey(path) ? null : overSwap ? 'swap' : 'move'
  $effect(() => {
    const current = tree
    untrack(() => { if (carry && carry.tree !== current) carry = null })
  })
  function startDrag(event, path) {
    if (disabled || !path.length || !event.dataTransfer) return
    dragging = { path, tree }
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData(PART_DRAG, '1')
    event.stopPropagation()
  }
  function endDrag() { dragging = null; overPath = null; overSwap = false }
  function dragover(event, path) {
    if (disabled || !event.dataTransfer) return
    if (event.dataTransfer.types.includes(PART_DRAG)) {
      if (!dragging || dragging.tree !== tree) return
      const isSwap = !siblings(dragging.path, path)
      if (isSwap && !swappable(dragging.path, path)) return
      event.preventDefault(); event.stopPropagation()
      event.dataTransfer.dropEffect = 'move'
      overPath = pathKey(path); overSwap = isSwap
      return
    }
    if (!event.dataTransfer.types.includes('text/plain')) return
    event.preventDefault(); event.stopPropagation()
    event.dataTransfer.dropEffect = 'copy'
    overPath = pathKey(path); overSwap = false
  }
  function drop(event, path) {
    overPath = null; overSwap = false
    if (event.dataTransfer?.types.includes(PART_DRAG)) {
      if (!dragging || dragging.tree !== tree) return
      const from = dragging.path, move = siblings(from, path), isSwap = !move && swappable(from, path)
      dragging = null
      if (!move && !isSwap) return
      event.preventDefault(); event.stopPropagation()
      if (move) reorder(from, path); else exchange(from, path)
      return
    }
    const given = event.dataTransfer?.getData('text/plain')?.trim()
    if (!given) return
    event.preventDefault(); event.stopPropagation()
    take(given, path)
  }
  function placeCarry(to) {
    const held = carry
    carry = null
    if (!held || held.tree !== tree) return
    if (siblings(held.path, to)) { reorder(held.path, to); focusChosen() }
    else if (swappable(held.path, to)) { exchange(held.path, to); focusChosen() }
  }

  let touch = null, ghost = $state(null), swallowed = false
  function touchDown(event) {
    if (event.pointerType !== 'touch' || disabled) return
    const box = event.target.closest?.('[data-hover-path]')
    if (!box || !treeEl?.contains(box)) return
    const path = parsePath(box.getAttribute('data-hover-path') ?? '')
    if (!path.length) return
    touch = { path, tree, pointer: event.pointerId, x: event.clientX, y: event.clientY, moved: false }
  }
  function touchMove(event) {
    if (!touch || event.pointerId !== touch.pointer) return
    if (!touch.moved) {
      if (Math.hypot(event.clientX - touch.x, event.clientY - touch.y) < 8) return
      if (touch.tree !== tree) { touch = null; return }
      touch.moved = true; swallowed = true
      dragging = { path: touch.path, tree }
      ghost = { text: text(partAt(tree, touch.path)) || EMPTY, x: event.clientX, y: event.clientY }
    } else if (ghost) ghost = { ...ghost, x: event.clientX, y: event.clientY }
    const under = document.elementFromPoint(event.clientX, event.clientY)?.closest?.('[data-hover-path]')
    const written = under && treeEl?.contains(under) ? under.getAttribute('data-hover-path') : null
    if (written == null) { overPath = null; overSwap = false; return }
    const path = parsePath(written)
    const move = siblings(touch.path, path), isSwap = !move && swappable(touch.path, path)
    if (!move && !isSwap) { overPath = null; overSwap = false; return }
    overPath = pathKey(path); overSwap = isSwap
  }
  function touchUp(event) {
    if (!touch || event.pointerId !== touch.pointer) return
    const from = touch.path, source = touch.tree, moved = touch.moved, landed = overPath
    touch = null
    if (!moved) return
    dragging = null; overPath = null; overSwap = false; ghost = null
    if (landed != null && source === tree) {
      const to = parsePath(landed)
      if (siblings(from, to)) reorder(from, to)
      else if (swappable(from, to)) exchange(from, to)
    }
    setTimeout(() => { swallowed = false }, 400)
  }
  function touchCancel() {
    if (!touch) return
    touch = null; dragging = null; overPath = null; overSwap = false; ghost = null
    setTimeout(() => { swallowed = false }, 400)
  }

  // Part context menu (right click, ContextMenu or Shift+F10), promoted to the top layer via
  // popover="auto" so it clears the enclosing <dialog> without clipping.
  let menu = $state(null), menuEl = $state(null)
  const menuPart = $derived(menu ? partAt(tree, menu.path) : undefined)
  async function openMenu(path, x, y, { keyboard = false } = {}) {
    if (disabled) return
    const W = 240, H = 260
    menu = { path, x: Math.max(8, Math.min(x, innerWidth - W - 8)), y: Math.max(8, Math.min(y, innerHeight - H - 8)) }
    choose(path, { toggle: false })
    await tick()
    if (menuEl && !menuEl.matches(':popover-open')) menuEl.showPopover()
    if (keyboard) menuEl?.querySelector('[role="menuitem"]:not([disabled])')?.focus()
    else menuEl?.focus({ preventScroll: true })
  }
  function closeMenu({ refocus = true } = {}) {
    if (!menu) return
    if (menuEl?.matches(':popover-open')) menuEl.hidePopover()
    menu = null
    if (refocus) focusChosen()
  }
  function runMenu(act) { act(); closeMenu() }
  function menuKey(event) {
    const items = [...(menuEl?.querySelectorAll('[role="menuitem"]:not([disabled])') ?? [])]
    const at = items.indexOf(document.activeElement)
    const dir = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key]
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu() }
    else if (event.key === 'Tab') closeMenu({ refocus: false })
    else if (dir) { event.preventDefault(); items[(at + dir + items.length) % items.length]?.focus() }
    else if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); items[event.key === 'Home' ? 0 : items.length - 1]?.focus() }
  }
  function partKey(event, path) {
    if (event.key === ' ' && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      if (!path.length || disabled) return
      if (carry && samePath(carry.path, path)) { carry = null; return }
      if (carry) { placeCarry(path); return }
      carry = { path, tree }
      choose(path, { toggle: false })
      return
    }
    if (event.key === 'Escape' && carry) { event.preventDefault(); event.stopPropagation(); carry = null; return }
    if (event.key === 'Escape' && !event.isComposing && !disabled && chosen !== null) { event.preventDefault(); event.stopPropagation(); choose(null); return }
    if (event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing && ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(event.key)) {
      const next = neighbor(tree, path, ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1)
      if (path.length && !disabled) { event.preventDefault(); if (next) { reorder(path, next); focusChosen() } }
      return
    }
    if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
      event.preventDefault()
      const r = event.currentTarget.getBoundingClientRect()
      openMenu(path, r.left + 8, r.bottom, { keyboard: true })
    }
  }
  function frameClick(event, path) {
    if (disabled) return
    const parts = event.currentTarget.querySelector(':scope > .ids-parts')
    if (event.target.closest('button') || parts?.contains(event.target)) return
    choose(path)
  }

  // Keys while the editor has the focus outside a text field: an operator for each key of the number
  // row and the row under it, by where it sits so any layout has them, Tab through the parts,
  // Backspace to empty one, Ctrl+Z to undo. A key the editor takes is its own: it is marked taken, so
  // the page's own shortcuts (a crop's review letters, the forms page's undo) leave it alone.
  function keydown(event) {
    if (event.isComposing || event.key === 'Process' || disabled) return
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"], .ids-context-menu')) return
    // Undo and redo work from anywhere in the editor; the focus stays in a picker or start-from-character search that has it.
    const command = event.ctrlKey || event.metaKey, inPicker = Boolean(event.target.closest?.('.ids-pick-tabs, .ids-pick-panel, .ids-start'))
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

{#snippet named(key, part)}
  {@const [before, after] = t(key, { part: '\u0000' }).split('\u0000')}{before}<FigureText text={part} />{after}
{/snippet}

{#snippet box(part, path, parent)}
  {@const selected = samePath(chosen, path)}
  {@const dragged = dragging !== null && samePath(dragging.path, path)}
  {@const carried = carry !== null && samePath(carry.path, path)}
  {#if isNode(part)}
    {@const what = parent ? t('form.ids.part', { part: name(part.op), n: parent.n, name: name(parent.op) }) : name(part.op)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="ids-node {layout(part.op)}" class:chosen={selected} class:dragged class:carried
      class:over={overKind(path) === 'move'} class:swap={overKind(path) === 'swap'}
      data-op={part.op} data-hover-path={pathKey(path)} draggable={!disabled && path.length > 0}
      title={path.length ? `${name(part.op)} · ${t('form.ids.dragSibling')}` : undefined}
      onclick={event => frameClick(event, path)} ondragstart={event => startDrag(event, path)} ondragend={endDrag}
      ondragover={event => dragover(event, path)} ondragleave={() => { overPath = null; overSwap = false }} ondrop={event => drop(event, path)}>
      <div class="ids-head">
        <button type="button" class="ids-op" data-path={pathKey(path)} aria-pressed={selected} aria-keyshortcuts="Space" {disabled}
          class:dragged class:carried draggable={!disabled && path.length > 0}
          aria-label={t('form.ids.node', { name: name(part.op), sequence: write(part) })}
          title={`${part.op} ${name(part.op)}${path.length ? ` · ${t('form.ids.dragSibling')}` : ''}`}
          onclick={() => choose(path)} oncontextmenu={event => { event.preventDefault(); openMenu(path, event.clientX, event.clientY) }}
          onkeydown={event => partKey(event, path)} ondragstart={event => startDrag(event, path)} ondragend={endDrag}
          ondragover={event => dragover(event, path)} ondragleave={() => { overPath = null; overSwap = false }} ondrop={event => drop(event, path)}><OperatorIcon op={part.op} size={14} /></button>
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
    <button type="button" class="ids-part" class:empty={part === null} class:chosen={selected} class:dragged class:carried
      class:over={overKind(path) === 'move'} class:swap={overKind(path) === 'swap'} aria-pressed={selected} aria-keyshortcuts="Space"
      data-path={pathKey(path)} data-hover-path={pathKey(path)} draggable={!disabled && path.length > 0} {disabled}
      title={`${part === null ? label : t('form.ids.choose', { part })}${path.length ? ` · ${t('form.ids.dragSibling')}` : ''}`}
      aria-label={parent ? t('form.ids.part', { part: label, n: parent.n, name: name(parent.op) }) : label}
      onclick={() => choose(path)} oncontextmenu={event => { event.preventDefault(); openMenu(path, event.clientX, event.clientY) }}
      onkeydown={event => partKey(event, path)} ondragstart={event => startDrag(event, path)} ondragend={endDrag}
      ondragover={event => dragover(event, path)} ondragleave={() => { overPath = null; overSwap = false }} ondrop={event => drop(event, path)}>{#if part === null}{EMPTY}{:else}<FigureText text={part} />{/if}</button>
  {/if}
{/snippet}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ids-composer" bind:this={root} onkeydown={keydown}>
  <div class="ids-start">
    <span class="ids-title">{t('form.ids.start')}</span>
    <div class="ids-start-row">
      <CharacterSearch bind:this={startSearch} compact codePoints bind:value={startQuery} label={t('form.ids.start')} placeholder={t('search.placeholder')}
        onselect={item => loadChar(item.char)} onsubmit={text => loadChar(text)} />
      <button type="button" class="ids-start-load" disabled={disabled || !startCandidate} onclick={() => loadChar(startQuery)}>{t('form.ids.start.load')}</button>
    </div>
    {#if startStatus === 'loading' && startPicked}
      <p class="ids-start-note">{t('form.ids.from.loading', { char: source })}</p>
    {:else if startStatus === 'failed' && startPicked}
      <p class="ids-start-note fault" role="status">
        {t('form.ids.from.failed', { char: source })}
        <button type="button" class="ids-start-retry" {disabled} onclick={() => loadChar(source, { again: true })}>{t('form.ids.from.retry')}</button>
      </p>
    {:else if startStatus === 'ready' && !startVariants.length && startPicked}
      <p class="ids-start-note" role="status">{source}: {t('form.ids.from.atomic')}</p>
    {:else if startVariants.length > 1}
      <div class="ids-start-shapes" role="group" aria-label={t('form.ids.start.shapes', { char: source })}>
        {#each startVariants as shape (shape.text)}
          <button type="button" class="ids-shape" class:active={whole === shape.text} aria-pressed={whole === shape.text} {disabled}
            title={shape.regions ? t('form.ids.from.regions', { regions: regionNames(shape.regions, base()) }) : shape.text}
            onclick={() => { if (whole !== shape.text || chosen !== null) change(shape.tree, null) }}>
            <span class="ids-shape-text" lang="ja">{shape.text}</span>{#if shape.regions}<span class="ids-shape-regions">{shape.regions}</span>{/if}
          </button>
        {/each}
      </div>
    {/if}
  </div>
  <div class="ids-field">
    <label class="ids-title" for={`${uid}-field`}>{t('form.ids.label')}</label>
    <div class="ids-field-row">
      <input id={`${uid}-field`} bind:this={field} value={draft} {disabled} lang="ja" spellcheck="false" autocomplete="off" enterkeyhint="done"
        placeholder="⿰⿱匕失⿱コ疋" aria-invalid={Boolean(problem)} oninput={edit} oncompositionend={() => edit()}
        onselect={selectField} onkeyup={selectField} onclick={selectField}
        onblur={() => { run = false; if (!typed.problem && tree !== null) draft = write(tree) }}
        onkeydown={event => { if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); use() } }} />
      <button type="button" class="ids-use" disabled={disabled || !usable} onclick={use}>{t('form.ids.use')}</button>
    </div>
  </div>
  <div class="ids-operators" role="group" aria-label={t('form.ids.operators')}>
    {#each OPERATORS as [op] (op)}
      <button type="button" class="ids-key" data-op={op} {disabled} aria-label={name(op)}
        title={t('form.ids.operatorTitle', { op, name: name(op), key: KEY[op] })} onclick={() => operator(op)}><OperatorIcon {op} /></button>
    {/each}
  </div>
  <div class="ids-view">
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div class="ids-tree" role="group" aria-label={t('form.ids.structure')} bind:this={treeEl}
      onpointerdown={touchDown} onpointermove={touchMove} onpointerup={touchUp} onpointercancel={touchCancel}
      onclickcapture={event => { if (swallowed) { swallowed = false; event.stopImmediatePropagation(); event.preventDefault() } }}>
      {@render box(tree, [], null)}
      {#if ghost}<div class="ids-touch-ghost" aria-hidden="true" style:left="{ghost.x}px" style:top="{ghost.y}px">{ghost.text}</div>{/if}
    </div>
    <IdsPreview {tree} sequence={whole} {chosen} />
  </div>
  <!-- svelte-ignore a11y_interactive_supports_focus -->
  <div class="ids-context-menu" popover="auto" role="menu" tabindex="-1" bind:this={menuEl}
    aria-label={menu ? t('form.ids.menu.label', { part: text(menuPart) || EMPTY }) : ''}
    style:left={menu ? `${menu.x}px` : undefined} style:top={menu ? `${menu.y}px` : undefined}
    ontoggle={event => { if (event.newState === 'closed' && menu) menu = null }} onkeydown={menuKey}>
    {#if menu}
      {@const filled = menuPart !== null && menuPart !== undefined}
      <p class="ids-menu-title">{isNode(menuPart) ? t('form.ids.menu.swap') : t('form.ids.menu.split')}</p>
      <div class="ids-menu-ops">
        {#each OPERATORS as [op] (op)}
          <button type="button" role="menuitem" class="ids-menu-op" title={name(op)} aria-label={name(op)}
            onclick={() => runMenu(() => operator(op, menu.path))}><OperatorIcon {op} size={18} /></button>
        {/each}
      </div>
      <hr class="ids-menu-sep" />
      <button type="button" role="menuitem" class="ids-menu-item" disabled={!filled} onclick={() => runMenu(() => empty(menu.path))}>
        <span>{t('form.ids.clear')}</span></button>
      <button type="button" role="menuitem" class="ids-menu-item" disabled={!isNode(menuPart)} onclick={() => runMenu(() => remove(menu.path))}>
        <span>{t('form.ids.unwrap')}</span></button>
      <button type="button" role="menuitem" class="ids-menu-item" disabled={!filled}
        onclick={() => { const seq = text(menuPart); runMenu(() => { navigator.clipboard?.writeText(seq).catch(() => {}) }) }}>
        <span>{t('form.ids.menu.copy')}</span><span class="ids-menu-seq" lang="ja">{text(menuPart)}</span></button>
    {/if}
  </div>
  <div class="ids-tools">
    <div class="ids-slot">
      <label class="ids-title" for={`${uid}-slot`}>{t('form.ids.slot')}</label>
      <div class="ids-slot-row">
        <input id={`${uid}-slot`} bind:this={slot} bind:value={piece} {disabled} lang="ja" spellcheck="false" autocomplete="off" enterkeyhint="done"
          placeholder={t('form.ids.slotHint')} onkeydown={event => { if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); enter() } }} />
        <button type="button" class="ids-apply" disabled={disabled || !pieceDirty} onclick={enter}>{t('form.ids.apply')}</button>
      </div>
    </div>
    <button type="button" class="ids-clear" disabled={disabled || !chosen || picked === null} onclick={() => empty(chosen)}>{t('form.ids.clear')}</button>
    <button type="button" class="ids-remove" disabled={disabled || !isNode(picked)} onclick={() => remove(chosen)}>{t('form.ids.unwrap')}</button>
    <button type="button" class="ids-undo" disabled={disabled || !past.length} title={t('forms.undo.title')} onclick={undo}>{t('forms.undo')}</button>
    <button type="button" class="ids-redo" disabled={disabled || !future.length} title={t('forms.redo.title')} onclick={redo}>{t('forms.redo')}</button>
  </div>
  {#if problem}<p class="ids-problem" role="status">{problem}</p>
  {:else if pending}<p class="ids-pending" role="status">{pending}</p>{/if}
  {#if rateForms.length}
    <fieldset class="ids-rate-forms">
      <legend class="ids-title">{t('chips.forms')} · {rateForms[0].host}</legend>
      <div class="ids-rate-options">
        {#each rateForms as form (form.id)}
          <button type="button" class="ids-rate-option" {disabled} aria-pressed={rateToken === form.text}
            title={form.text} onclick={() => selectRateForm(form)}>
            {#if rateDrawing}
              <svg viewBox="0 0 1000 1000" aria-hidden="true">
                <path class="rate-sans" d={rateDrawing(form.text, 'sans')} />
                <path class="rate-serif" d={rateDrawing(form.text, 'serif')} />
              </svg>
            {/if}
            <span lang="zh-Hans">{form.label}</span>
          </button>
        {/each}
      </div>
    </fieldset>
  {/if}
  {#if typeof picked === 'string' && ((swaps[lookupChar(picked)] ?? []).length || splits.length || figured.length)}
    <div class="ids-swaps" role="group" aria-label={t('form.ids.swaps', { part: picked })}>
      <span class="ids-title">{@render named('form.ids.swaps', picked)}</span>
      <div class="ids-options">
        {#each figured as figure (figure.char)}
          <button type="button" class="ids-swap ids-figure" {disabled} title={[point(figure.char), figure.mj].filter(Boolean).join(' · ')} aria-label={[figure.char, point(figure.char), figure.mj].filter(Boolean).join(' ')}
            onclick={() => place(figure.char)}><FigureText text={figure.char} /></button>
        {/each}
        {#each swaps[lookupChar(picked)] ?? [] as item (item.char)}
          <button type="button" class="ids-swap" {disabled} title={`${picked} → ${item.char} · ${item.count}`} onclick={() => swap(item)}>{item.char}</button>
        {/each}
        {#each splits as sequence (sequence)}
          <button type="button" class="ids-swap ids-split" {disabled} title={t('form.ids.split', { part: picked })} onclick={() => split(sequence)}>{sequence}</button>
        {/each}
      </div>
    </div>
  {/if}
  <div class="ids-search">
    <span class="ids-title">{#if chosen && typeof picked === 'string'}{@render named('form.ids.replace', picked)}{:else}{chosen && picked !== null ? t('form.ids.replace', { part: write(picked) }) : t('form.ids.insert')}{/if}</span>
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
  <p class="ids-keys">{t('form.ids.keys')} <a class="ids-sources" href={SOURCES} rel="noopener" target="_blank">{t('form.ids.sources')}</a></p>
</div>

<style>
  .ids-composer{--ids-font:var(--char-font);display:flex;flex-direction:column;gap:10px}
  .ids-start{display:flex;flex-direction:column;gap:4px}
  .ids-start-row{display:flex;align-items:stretch;gap:6px}
  .ids-start-row :global(.character-search){flex:1;min-width:0}
  .ids-start-load{flex:none;padding:5px 12px;font-size:13px;white-space:nowrap}
  .ids-start-note{margin:0;font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .ids-start-note.fault{color:var(--fault)}
  .ids-start-retry{padding:2px 8px;font-size:12px}
  .ids-start-shapes{display:flex;flex-wrap:wrap;gap:6px}
  .ids-shape{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;font-size:15px;line-height:1.3}
  .ids-shape-text{font-family:var(--ids-font)}
  .ids-shape-regions{font-size:10px;letter-spacing:.08em;padding:1px 4px;border-radius:4px;background:var(--surface-selected);color:var(--muted);font-variant-numeric:tabular-nums}
  .ids-shape:not(:disabled):hover,.ids-shape.active{border-color:var(--accent)}
  .ids-shape.active{background:var(--accent-light)}
  .ids-field,.ids-slot{display:flex;flex-direction:column;gap:4px}
  .ids-title{font-size:11px;color:var(--muted)}
  .ids-field-row{display:flex;align-items:stretch;gap:8px;flex-wrap:wrap}
  .ids-field-row input{flex:1 1 200px;min-width:0}
  .ids-field input{font-size:20px;padding:6px 8px;font-family:var(--ids-font)}
  .ids-field input[aria-invalid="true"]{border-color:var(--fault)}
  .ids-use{flex:none;min-height:40px;padding:6px 16px;font-size:13px;font-weight:500;white-space:nowrap;
    border-color:var(--accent-solid);background:var(--accent-solid);color:var(--on-color)}
  .ids-use:not(:disabled):hover{border-color:var(--accent-solid-hover);background:var(--accent-solid-hover)}
  .ids-operators{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:4px}
  @media (max-width:520px){.ids-operators{grid-template-columns:repeat(6,minmax(0,1fr))}}
  .ids-key{display:grid;place-items:center;padding:5px 0;min-width:0}
  .ids-key:not(:disabled):hover,.ids-key:focus-visible{border-color:var(--accent)}

  .ids-view{display:flex;align-items:flex-start;gap:12px}
  .ids-view > .ids-tree{flex:1;min-width:0}
  @media (max-width:520px){.ids-view{flex-direction:column-reverse;align-items:center}.ids-view > .ids-tree{align-self:stretch}}
  .ids-tree{display:grid;justify-items:center;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface-subtle);min-height:72px}
  .ids-tree [data-hover-path]{touch-action:none}
  .ids-node{position:relative;display:flex;flex-direction:column;min-width:0;padding:22px 6px 6px;
    border:1.5px dashed var(--line-strong);border-radius:8px;cursor:pointer}
  .ids-node[draggable="true"],.ids-op[draggable="true"],.ids-part[draggable="true"]{cursor:grab}
  .ids-node[draggable="true"]:active,.ids-op[draggable="true"]:active,.ids-part[draggable="true"]:active{cursor:grabbing}
  .ids-tree > .ids-node,.ids-tree > .ids-part{width:min(100%,360px)}
  .ids-node.chosen,.ids-node.swap{border-style:solid;border-color:var(--accent);background:var(--accent-light)}
  .ids-node.over{border-style:solid;border-color:var(--accent)}
  .ids-node.dragged,.ids-op.dragged,.ids-part.dragged{opacity:.45}
  .ids-node.carried,.ids-part.carried{outline:2px dashed var(--accent);outline-offset:2px}
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
  .ids-part.chosen,.ids-part.swap{border:2px solid var(--accent);padding:1px 5px;background:var(--accent-light)}
  .ids-part.over{border:2px solid var(--accent);padding:1px 5px}
  .ids-op:focus-visible,.ids-part:focus-visible{outline:2px solid var(--focus-ring);outline-offset:1px}
  .ids-touch-ghost{position:fixed;z-index:60;max-width:12em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;pointer-events:none;
    padding:4px 10px;border:1px solid var(--accent);border-radius:10px;background:var(--surface);color:var(--ink);
    font-family:var(--ids-font);font-size:20px;line-height:1.3;box-shadow:0 8px 20px var(--shadow);transform:translate(-50%,-50%)}

  .ids-context-menu{position:fixed;inset:auto;margin:0;padding:6px;width:max-content;max-width:260px;
    border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);box-shadow:0 10px 30px var(--shadow-menu)}
  .ids-context-menu:popover-open{display:flex;flex-direction:column;gap:2px}
  .ids-menu-title{margin:2px 6px 4px;font-size:11px;color:var(--muted)}
  .ids-menu-ops{display:grid;grid-template-columns:repeat(6,32px);gap:2px}
  .ids-menu-op{display:grid;place-items:center;height:30px;padding:0;border-color:transparent;background:none}
  .ids-menu-op:hover,.ids-menu-op:focus-visible{background:var(--accent-light);border-color:transparent}
  .ids-menu-sep{border:0;height:1px;margin:4px 2px;background:var(--line)}
  .ids-menu-item{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:6px 8px;font-size:13px;text-align:left;
    border-color:transparent;background:none}
  .ids-menu-item:not(:disabled):hover,.ids-menu-item:focus-visible{background:var(--surface-selected);border-color:transparent}
  .ids-menu-seq{font-family:var(--ids-font);font-size:14px;color:var(--muted)}

  .ids-tools{display:flex;flex-wrap:wrap;align-items:flex-end;gap:6px}
  .ids-slot{flex:1 1 160px;min-width:0}
  .ids-slot-row{display:flex;align-items:stretch;gap:6px}
  .ids-slot-row input{flex:1;min-width:0}
  .ids-slot input{font-size:14px;padding:7px 8px;min-height:38px}
  .ids-slot input:not(:placeholder-shown){font-size:18px;padding:5px 8px;font-family:var(--ids-font)}
  .ids-apply{flex:none;min-height:38px;padding:5px 11px;font-size:13px;white-space:nowrap}
  .ids-tools > button{min-height:38px;padding:5px 9px;font-size:13px}
  .ids-rate-forms{border:0;margin:0;padding:0;min-width:0}
  .ids-rate-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;max-width:29em;margin-top:4px}
  .ids-rate-option{display:flex;align-items:center;gap:.6em;padding:5px 8px;font-size:14px}
  .ids-rate-option svg{width:2.6em;height:2.6em;flex:none;fill:currentColor}
  .rate-serif,:global(:root[data-typeface=serif]) .rate-sans{display:none}
  :global(:root[data-typeface=serif]) .rate-serif{display:inline}
  .ids-rate-option[aria-pressed=true]{border-color:var(--accent);background:var(--surface-selected)}
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
  .ids-keys{margin:0;font-size:11px;line-height:1.5;color:var(--muted)}
  .ids-sources{color:var(--accent);text-decoration:underline;text-underline-offset:2px}
</style>
