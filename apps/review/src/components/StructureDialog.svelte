<script>
  // The 構形 of a character's forms, laid out to arrange: each 構形 is a tray holding the forms built
  // so, and a signed-in reader moves a form to another tray (has_structure), so the form stands under
  // that 構形 for every crop, or moves a whole tray into another (folds_into), so the two are one 構形
  // and the first stands as a tray within the second. A form or a tray is moved by dragging it, by
  // picking it up with a click and clicking a tray, or from the keyboard: Enter picks it up, the
  // arrows aim at a tray, Enter puts it down and Escape lets go. A form the reader filed, and a tray
  // the reader folded, carries a × that takes the reader's word back; so does the strip that shows at
  // the end of the board while it is held, and putting a form back on the tray its description gives.
  // A tray stays on the board once shown, emptied or not, and a search adds the 構形 of any other
  // character to it.
  import { onMount, tick, untrack } from 'svelte'
  import { flip } from 'svelte/animate'
  import { crossfade } from 'svelte/transition'
  import { cubicOut } from 'svelte/easing'
  import FormText from './FormText.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import { setStructure, setFold } from '../lib/cropForms.js'
  import { composable, headTarget, NAMES, proposed } from '../lib/structures.js'
  import { isDescription } from '../lib/ids.js'
  import { signedInUser } from '../lib/session.svelte.js'
  import { t } from '../lib/i18n.svelte.js'
  // `forms` are the forms to lay out (`{ char, script, lang, regions }`), `of` the character whose
  // regional shapes are among them, `known` what the opener has read of their 構形 and `read` how to
  // read it again (`(names, known) => Map`). `current` is marked as the form the reader came from.
  let { of = '', forms = [], known: opened = new Map(), read, current = null, close, changed = () => {} } = $props()
  const id = $props.id()
  let dialog, board = $state(null)
  let known = $state(untrack(() => opened)), added = $state([]), query = $state('')
  // The trays the board has shown, and which of them stand under which, folded by a reader or as a layout of it (`folds`, by the
  // folded tray's id: `{ head, by }`). What is held is a form (`held`, its text) or a tray (`lifted`,
  // its id), and `over` is the tray aimed at.
  let trays = $state([]), folds = $state(new Map()), held = $state(null), lifted = $state(null), over = $state(null), dragging = $state(false)
  let busy = $state(null), landed = $state(null), error = $state(''), notice = $state(''), said = $state('')
  const me = $derived(signedInUser()?.id ?? null)
  const listed = $derived.by(() => {
    const seen = new Set()
    return [...forms, ...added].filter(form => composable(form.char) && !seen.has(form.char) && seen.add(form.char))
  })
  // The 構形 a form's description gives it, as first read: putting the form back there takes the
  // reader's filing back.
  const home = new Map()
  // What names a tray's 構形 in a save: an encoded character its description puts there, else a
  // described form, else a regional shape of `of`, else its id.
  const naming = form => form.regions ? [1, { char: form.char, of }] : isDescription(form.char) ? [2, { char: form.char }] : [3, { char: form.char }]
  function absorb(found) {
    const next = [...trays], links = new Map(), filled = new Set()
    const trayOf = (id, label) => next.find(tray => tray.id === id) ?? next[next.push({ id, label, rank: 0, target: { structure: id } }) - 1]
    for (const form of listed) {
      const stands = found.get(form.char)
      if (!stands) continue
      trayOf(stands.id, stands.label)
      const tray = stands.part ? trayOf(stands.part.id, stands.part.label) : trayOf(stands.id, stands.label)
      filled.add(tray.id)
      if (stands.part) links.set(stands.part.id, { head: stands.id, by: stands.part.by ?? [] })
      if (stands.by) continue
      home.set(form.char, tray.id)
      const [rank, target] = naming(form)
      if (rank > tray.rank) Object.assign(tray, { rank, target })
      // A tray that only layouts of it stand under (⿺辶禺 by 𽓻 alone) is named by one of them.
      const named = headTarget(stands, target), head = named && trayOf(stands.id, stands.label)
      if (head && !head.rank) Object.assign(head, { rank: 0.5, target: named })
    }
    // A tray with no form left on the board keeps the fold it was last seen in, led on to where that ends now.
    for (const [part, link] of folds) {
      if (filled.has(part) || links.has(part)) continue
      let head = link.head
      for (let step = 0; step < 8 && links.has(head); step += 1) head = links.get(head).head
      if (head !== part && !links.has(head)) links.set(part, { ...link, head })
    }
    known = found; trays = next; folds = links
  }
  // The tray a form stands in: the 構形 it is built as, folded or not.
  const innerOf = char => { const stands = known.get(char); return stands ? stands.part?.id ?? stands.id : null }
  // The tray a folded tray ends in, however many folds lead there.
  const headOf = id => { let at = id; for (let step = 0; step < 8 && folds.has(at); step += 1) at = folds.get(at).head; return at }
  const labelOf = id => trays.find(tray => tray.id === id)?.label ?? ''
  const filled = $derived(trays.map(tray => ({ ...tray, forms: listed.filter(form => innerOf(form.char) === tray.id) })))
  const heads = $derived(filled.filter(tray => !folds.has(tray.id)).map(tray => ({ ...tray, parts: filled.filter(part => folds.has(part.id) && headOf(part.id) === tray.id) })))
  const loose = $derived(listed.filter(form => !known.get(form.char)))
  // How a form stands in a tray, folded or not, for a move shown before the site answers.
  const standsIn = (id, by) => {
    const link = folds.get(id), head = link ? headOf(id) : id
    return { id: head, label: labelOf(head), by, others: [], part: link ? { id, label: labelOf(id), by: link.by } : null }
  }
  // The 構形 the reader's own filing of a form names: another reader's may hold the form elsewhere.
  const mineOf = char => {
    const stands = known.get(char)
    if (!me || !stands) return null
    return stands.by?.includes(me) ? stands.part?.id ?? stands.id : (stands.others ?? []).find(other => other.by.includes(me))?.id ?? null
  }
  const foldedByMe = id => Boolean(me && folds.get(id)?.by.includes(me))
  // Where what is held can be put. A form goes to every tray but the one it stands in; a tray into
  // every tray that is not itself folded, but its own. Either goes out of the reader's own word for it.
  const OUT = 'out'
  const out = $derived(held && mineOf(held) ? t('structure.take', { form: held }) : lifted && foldedByMe(lifted) ? t('structure.split', { structure: labelOf(lifted) }) : '')
  // A proposed form can be filed where it stands, which confirms it.
  const proposedHere = char => known.has(char) && proposed(char, known.get(char).by)
  const targets = $derived.by(() => {
    const open = held ? trays.filter(tray => tray.id !== innerOf(held) || proposedHere(held))
      : lifted ? trays.filter(tray => tray.id !== lifted && !folds.has(tray.id) && tray.id !== headOf(lifted)) : []
    return [...open.map(tray => tray.id), ...(out ? [OUT] : [])]
  })
  // What a tray says of what is held, to a reader who cannot see where it would go.
  const offer = tray => held ? `${tray.label} · ${t('structure.place', { form: held })}` : t('structure.merge', { structure: tray.label })
  // A form or a tray on its way out is inert while it leaves; the one that stands is the other.
  const chipOf = char => board?.querySelector(`.token:not([inert]) > [data-form="${CSS.escape(char)}"]`)
  const handleOf = tray => board?.querySelector(`.tray:not([inert]) > h3 > [data-handle="${CSS.escape(tray)}"]`)
  const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches
  const [send, receive] = crossfade({ duration: () => calm() ? 0 : 280, easing: cubicOut })
  const slide = { duration: () => calm() ? 0 : 220, easing: cubicOut }
  // What the site holds of `names`, NAMES to a request, put over what the board knows. A reading
  // asked before a later one, or before a move, is dropped when it answers.
  let reads = 0
  async function refresh(names) {
    const turn = ++reads, asked = [...new Set(names)].filter(composable), chunks = []
    for (let from = 0; from < asked.length; from += NAMES) chunks.push(asked.slice(from, from + NAMES))
    const found = await Promise.all(chunks.map(chunk => read(chunk, new Map())))
    if (turn === reads) absorb(new Map([...[...known].filter(([char]) => !asked.includes(char)), ...found.flatMap(part => [...part])]))
  }
  let wrote = false
  // A move shows at once and is read again once saved: a save the site refuses puts things back, and
  // one other readers outweigh says so. `mark` is what moved, a form's text or `{ tray }`, which keeps
  // the focus, and `to` the tray the reader meant it to stand in.
  async function commit(mark, to, change, save) {
    const before = [known, folds], item = () => mark.tray ? labelOf(mark.tray) : mark
    const focus = () => (mark.tray ? handleOf(mark.tray) : chipOf(mark))?.focus({ preventScroll: true })
    // The opener reads again once the board closes, also when it closes before the site has answered.
    busy = mark.tray ?? mark; error = ''; notice = ''; wrote = true; reads += 1
    change()
    await tick()
    focus()
    let saved = true
    try { await save() } catch (failure) { saved = false; [known, folds] = before; error = failure.message }
    // Read again either way: the site may hold a save whose answer was lost.
    try { await refresh(listed.map(form => form.char)) } catch (failure) { if (saved) error = failure.message }
    busy = null
    const stood = mark.tray ? headOf(mark.tray) : innerOf(mark)
    // A tray split off a fold that is a layout of another 構形 stands under that one, with nobody named.
    const laid = mark.tray && folds.has(mark.tray) && !folds.get(mark.tray).by.length
    if (saved && to && stood !== to && !laid) notice = t('structure.disputed', { item: item() })
    said = [...new Set([item(), labelOf(stood)])].filter(Boolean).join(' · ')
    await tick()
    focus()
  }
  function place(char, tray) {
    if (innerOf(char) === tray.id && !proposedHere(char)) return
    if (mineOf(char) && home.get(char) === tray.id) return takeOut(char)
    return commit(char, tray.id, () => { known = new Map([...known, [char, standsIn(tray.id, [me])]]); landed = tray.id }, () => setStructure(char, tray.target))
  }
  function takeOut(char) {
    const mine = mineOf(char), back = home.get(char)
    if (!mine || busy) return
    release()
    return commit(char, back, () => { if (back) { known = new Map([...known, [char, standsIn(back, null)]]); landed = back } }, () => setStructure(char, { structure: mine }, { remove: true }))
  }
  // One tray folded into another: its forms, and the trays folded into it, stand under that one.
  function fold(from, into) {
    const tray = trays.find(tray => tray.id === from), head = trays.find(tray => tray.id === into)
    if (!tray || !head || busy) return
    return commit({ tray: from }, into, () => {
      const part = { id: from, label: tray.label, by: [me] }
      known = new Map([...known].map(([char, stands]) => innerOf(char) === from ? [char, { ...stands, id: into, label: head.label, part }]
        : stands.id === from ? [char, { ...stands, id: into, label: head.label }] : [char, stands]))
      folds = new Map([...[...folds].map(([part, link]) => [part, link.head === from ? { ...link, head: into } : link]), [from, { head: into, by: [me] }]])
      landed = into
    }, () => setFold(tray.target, head.target))
  }
  // The reader's fold of a tray taken back: it stands on its own again, unless another reader folds it too.
  function split(from) {
    const tray = trays.find(tray => tray.id === from)
    if (!tray || !foldedByMe(from) || busy) return
    release()
    return commit({ tray: from }, from, () => {
      known = new Map([...known].map(([char, stands]) => stands.part?.id === from ? [char, { ...stands, id: from, label: tray.label, part: null }] : [char, stands]))
      folds = new Map([...folds].filter(([part]) => part !== from))
      landed = from
    }, () => setFold({ structure: from }, null))
  }
  function release() { held = null; lifted = null; over = null; dragging = false }
  const holding = (kind, key) => (kind === 'form' ? held : lifted) === key
  function pick(kind, key) {
    if (busy || !me) return
    if (holding(kind, key)) return release()
    release()
    if (kind === 'form') held = key
    else lifted = key
  }
  function drop(to) {
    const char = held, from = lifted, tray = trays.find(tray => tray.id === to)
    if (to === OUT) return char ? takeOut(char) : split(from)
    release()
    if (tray && char) place(char, tray)
    else if (tray && from) fold(from, tray.id)
  }

  // A drag with a mouse or a pen; a touch picks up with a tap, so the board still scrolls.
  let grip = null, skipClick = false
  // The tray a point or a click aims at. A form goes to the tray it is put in; a tray into the
  // nearest tray around the point that can take it.
  function aim(element) {
    const inner = element?.closest?.('[data-tray]')
    if (held) return inner && targets.includes(inner.dataset.tray) ? inner.dataset.tray : null
    for (let tray = inner; tray; tray = tray.parentElement?.closest('[data-tray]')) if (targets.includes(tray.dataset.tray)) return tray.dataset.tray
    return null
  }
  const trayAt = (x, y) => aim(document.elementsFromPoint(x, y).find(element => !grip.node.contains(element)))
  function down(event, kind, key) {
    if (busy || !me || event.button !== 0 || !event.isPrimary || event.pointerType === 'touch') return
    grip = { pointer: event.pointerId, x: event.clientX, y: event.clientY, node: event.currentTarget.closest(kind === 'form' ? '.token' : '.tray'), kind, key, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function move(event) {
    if (!grip || event.pointerId !== grip.pointer) return
    const dx = event.clientX - grip.x, dy = event.clientY - grip.y
    if (!grip.moved) {
      if (Math.hypot(dx, dy) < 6) return
      release()
      grip.moved = true; dragging = true
      if (grip.kind === 'form') held = grip.key
      else lifted = grip.key
    }
    grip.node.style.translate = `${dx}px ${dy}px`
    over = trayAt(event.clientX, event.clientY)
  }
  function settle(node) {
    const from = node.style.translate
    node.style.translate = ''
    if (!calm()) node.animate([{ translate: from }, { translate: '0 0' }], { duration: 180, easing: 'ease-out' })
  }
  function up(event) {
    if (!grip || event.pointerId !== grip.pointer) return
    const { node, moved } = grip
    grip = null
    if (!moved) return
    // The click that ends a drag is the drag's, not a pick.
    skipClick = true; setTimeout(() => { skipClick = false })
    // What is put down leaves from where it was let go; what is let go elsewhere goes back.
    if (!over) { settle(node); release(); return }
    drop(over)
    // What moved has left this node behind; what stays where it was goes back to its place.
    tick().then(() => { if (node.isConnected && !node.inert) settle(node) })
  }
  function lost(event) {
    if (!grip || event.pointerId !== grip.pointer) return
    const { node, moved } = grip
    grip = null
    if (moved) { settle(node); release() }
  }
  function tap(event, kind, key) {
    if (skipClick) { skipClick = false; event.stopPropagation(); return }
    // With something else held, the click is the board's: what is held is put down or let go.
    if ((held || lifted) && !holding(kind, key)) return
    event.stopPropagation()
    pick(kind, key)
  }
  let spaced = false
  function keys(event, kind, key) {
    if (!holding(kind, key)) return
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    if (step) {
      event.preventDefault()
      // The arrows follow the trays as they stand on the board.
      const order = [...board.querySelectorAll('[data-tray]')].map(element => element.dataset.tray).filter(to => targets.includes(to)), at = order.indexOf(over)
      over = order[at < 0 ? (step > 0 ? 0 : order.length - 1) : (at + step + order.length) % order.length] ?? null
      const tray = trays.find(tray => tray.id === over)
      said = over === OUT ? out : tray ? offer(tray) : ''
      if (over) board.querySelector(`[data-tray="${CSS.escape(over)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    } else if ((event.key === 'Enter' || event.key === ' ') && over) {
      event.preventDefault()
      spaced = event.key === ' '
      drop(over)
    } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); release() }
  }
  // Space activates a button as the key comes up, which would pick up again what has just landed.
  function keyup(event) { if (event.key === ' ' && spaced) { spaced = false; event.preventDefault() } }
  // A click with something held puts it down on the tray clicked, or lets it go.
  function clicked(event) {
    if (!held && !lifted) return
    const to = aim(event.target)
    if (to) drop(to)
    else release()
  }
  // A click on the backdrop closes the board; one on the board's own margins only lets go.
  function margin(event) {
    if (event.target !== dialog) return
    const box = dialog.getBoundingClientRect()
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close()
    else release()
  }

  async function add(item) {
    query = ''
    if (!composable(item.char)) return
    if (!listed.some(form => form.char === item.char)) {
      added = [...added, { char: item.char, script: item.script ?? '', lang: item.lang ?? '' }]
      try { await refresh([item.char]) } catch (failure) { error = failure.message }
    }
    await tick()
    const chip = chipOf(item.char)
    chip?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    chip?.focus({ preventScroll: true })
  }
  const titleOf = form => form.regions ? `${form.char} ${form.regions}` : proposedHere(form.char) ? `${form.char} · ${t('chips.derived')}` : form.char
  function cancel(event) { if (held || lifted) { event.preventDefault(); release() } }
  function closed() { if (wrote) changed(); close() }
  onMount(() => {
    absorb(opened)
    dialog.showModal()
    // `showModal` focuses the close button; the work starts at the form the reader came from.
    if (current) tick().then(() => chipOf(current)?.focus({ preventScroll: true }))
    refresh(listed.map(form => form.char)).catch(failure => { error = failure.message })
  })
</script>

{#snippet token(form)}
  {@const mine = Boolean(mineOf(form.char))}
  <button type="button" class="piece" class:current={form.char === current} data-form={form.char} aria-pressed={held === form.char} aria-busy={busy === form.char}
    aria-disabled={!me || Boolean(busy)} title={titleOf(form)} aria-label={titleOf(form)}
    onpointerdown={event => down(event, 'form', form.char)} onpointermove={move} onpointerup={up} onpointercancel={lost} onlostpointercapture={lost}
    onclick={event => tap(event, 'form', form.char)} onkeydown={event => keys(event, 'form', form.char)}>
    <FormText text={form.char} script={form.script} lang={form.lang} />{#if proposedHere(form.char)}<sup aria-hidden="true">*</sup>{/if}
  </button>{#if mine}<button type="button" class="take" disabled={Boolean(busy)} aria-label={t('structure.take', { form: form.char })} title={t('structure.take', { form: form.char })}
    onclick={event => { event.stopPropagation(); takeOut(form.char) }}>×</button>{/if}
{/snippet}

{#snippet tokens(list)}
  {#each list as form (form.char)}
    <span class="token" class:mine={Boolean(mineOf(form.char))} class:held={held === form.char} class:dragging={dragging && held === form.char} class:saving={busy === form.char}
      in:receive={{ key: form.char }} out:send={{ key: form.char }} animate:flip={slide}>{@render token(form)}</span>
  {/each}
{/snippet}

<!-- A tray's caption is its handle: the tray is picked up, dragged and put down by it. -->
{#snippet caption(tray)}
  <h3 lang="zh">
    <button type="button" class="handle" data-handle={tray.id} aria-pressed={lifted === tray.id} aria-busy={busy === tray.id} aria-disabled={!me || Boolean(busy)}
      onpointerdown={event => down(event, 'tray', tray.id)} onpointermove={move} onpointerup={up} onpointercancel={lost} onlostpointercapture={lost}
      onclick={event => tap(event, 'tray', tray.id)} onkeydown={event => keys(event, 'tray', tray.id)}>{tray.label}</button>{#if foldedByMe(tray.id)}<button type="button" class="take" disabled={Boolean(busy)}
      aria-label={t('structure.split', { structure: tray.label })} title={t('structure.split', { structure: tray.label })} onclick={event => { event.stopPropagation(); split(tray.id) }}>×</button>{/if}
  </h3>
{/snippet}

{#snippet body(tray)}
  {@render caption(tray)}
  <div class="tray-forms">
    {@render tokens(tray.forms)}
    {#if over === tray.id && held}<span class="ghost" aria-hidden="true"><FormText text={held} /></span>{/if}
  </div>
{/snippet}

<svelte:window onkeyup={keyup} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog class="structure-dialog" bind:this={dialog} aria-labelledby="{id}-title" oncancel={cancel} onclose={closed} onclick={margin}>
  <header>
    <h2 class="overline" id="{id}-title">{t('structure.arrange')}</h2>
    <button type="button" class="icon-button" aria-label={t('structure.close')} onclick={() => dialog.close()}>×</button>
  </header>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="board" class:holding={held || lifted} class:dragging bind:this={board} onclick={clicked}>
    {#each heads as tray (tray.id)}
      <section class="tray" class:target={targets.includes(tray.id)} class:over={over === tray.id} class:lifted={lifted === tray.id} class:dragging={dragging && lifted === tray.id}
        class:saving={busy === tray.id} class:landed={landed === tray.id} onanimationend={() => { if (landed === tray.id) landed = null }} data-tray={tray.id}
        in:receive={{ key: 'tray ' + tray.id }} out:send={{ key: 'tray ' + tray.id }} animate:flip={slide}
        aria-label={targets.includes(tray.id) ? offer(tray) : tray.label}>
        {@render body(tray)}
        {#each tray.parts as part (part.id)}
          <section class="tray part" class:target={targets.includes(part.id)} class:over={over === part.id} class:lifted={lifted === part.id} class:dragging={dragging && lifted === part.id}
            class:saving={busy === part.id} class:landed={landed === part.id} onanimationend={event => { event.stopPropagation(); if (landed === part.id) landed = null }} data-tray={part.id}
            in:receive={{ key: 'tray ' + part.id }} out:send={{ key: 'tray ' + part.id }} animate:flip={slide}
            aria-label={targets.includes(part.id) ? offer(part) : part.label}>
            {@render body(part)}
          </section>
        {/each}
        {#if over === tray.id && lifted}<div class="ghost within" lang="zh" aria-hidden="true">{labelOf(lifted)}</div>{/if}
      </section>
    {/each}
    {#if loose.length}
      <section class="tray loose"><div class="tray-forms">{@render tokens(loose)}</div></section>
    {/if}
    {#if out}
      <button type="button" class="tray out target" class:over={over === OUT} data-tray={OUT}>{out}</button>
    {/if}
  </div>
  <footer>
    <CharacterSearch compact codePoints bind:value={query} label={t('form.add')} placeholder={t('search.placeholder')} onselect={add} />
    {#if error}<p class="structure-error" role="alert">{error}</p>{/if}
    {#if notice}<p class="structure-notice" role="status">{notice}</p>{/if}
  </footer>
  <p class="said" aria-live="polite">{said}</p>
</dialog>

<style>
  .structure-dialog{inset:0;margin:auto;width:min(920px,calc(100vw - 32px));height:fit-content;max-height:calc(100dvh - 32px);padding:20px 24px 24px;
    border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--ink);box-shadow:0 20px 60px var(--shadow);overflow:auto;overscroll-behavior:contain}
  .structure-dialog[open]{display:flex;flex-direction:column;gap:16px}
  .structure-dialog::backdrop{background:light-dark(#16151f40, rgb(0 0 0 / 50%));overflow:hidden;overscroll-behavior:contain}
  header{display:flex;align-items:center;justify-content:space-between;gap:12px}
  header h2{font-weight:500}
  .board{display:flex;flex-wrap:wrap;align-items:flex-start;gap:12px}
  .board.dragging,.board.dragging :global(*){cursor:grabbing}
  .tray{display:flex;flex-direction:column;gap:8px;min-width:120px;padding:10px 12px 12px;border:1px solid var(--line);border-radius:10px;background:var(--paper);
    transition:border-color .15s,background-color .15s,box-shadow .15s}
  .tray h3{display:flex;align-items:stretch;margin:0;font-size:15px;font-weight:400;line-height:1.3}
  /* A caption is the tray's handle, marked by a grip. */
  .handle{display:inline-flex;align-items:center;gap:6px;border:0;border-radius:4px;background:transparent;padding:0;font:inherit;color:inherit;text-align:left;cursor:grab;user-select:none;-webkit-user-select:none}
  .handle::before{content:"";flex:none;width:6px;height:10px;opacity:.45;background:radial-gradient(circle,currentColor 1px,transparent 1.2px) 0 0/3px 3.34px}
  .handle[aria-disabled="true"]{cursor:default}
  .handle[aria-disabled="true"]::before{display:none}
  .handle:not([aria-disabled="true"]):hover{color:var(--accent)}
  .tray h3 .take{margin-left:6px;border:0;border-radius:4px;background:transparent;padding:0 4px}
  /* A tray folded into another stands within it. */
  .tray.part{min-width:0;padding:8px 10px 10px;background:var(--surface)}
  /* A tray on its way out leaves the trays within it to their new places. */
  .tray[inert] .tray.part{visibility:hidden}
  .tray.lifted{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent)}
  .tray.dragging{position:relative;z-index:3;scale:1.02;box-shadow:0 10px 24px var(--shadow)}
  .tray.saving{opacity:.7}
  .ghost.within{align-self:flex-start;font-size:15px;padding:8px 10px}
  .tray.loose{border-style:dotted;background:transparent}
  .tray-forms{position:relative;display:flex;flex-wrap:wrap;gap:6px;min-height:36px}
  /* A tray the held form can go to shows it; the one aimed at is filled. */
  .tray.out{flex:1 1 100%;align-items:center;justify-content:center;min-height:44px;font-size:13px;color:var(--accent);background:transparent}
  .tray.target{border-style:dashed;border-color:var(--accent);cursor:copy}
  .tray.over{border-style:solid;background:var(--accent-light);box-shadow:0 0 0 2px var(--accent)}
  /* The tray a form has just gone to shows it once. */
  .tray.landed{animation:landed .7s ease-out}
  @keyframes landed{from{background:var(--accent-light);box-shadow:0 0 0 2px var(--accent)}}
  .token{display:inline-flex;align-items:stretch;border-radius:7px}
  .piece sup{font-size:11px;line-height:1;color:var(--muted);margin-left:-4px;align-self:flex-start}
  .piece{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;font-size:22px;line-height:1.2;cursor:grab;user-select:none;-webkit-user-select:none;
    font-family:var(--char-font)}
  .piece[aria-disabled="true"]{cursor:default}
  .piece:not([aria-disabled="true"]):hover{border-color:var(--accent)}
  .piece.current{border-color:var(--line-strong);box-shadow:inset 0 -2px 0 var(--line-strong)}
  .token.mine .piece{border-right:0;border-top-right-radius:0;border-bottom-right-radius:0}
  .take{padding:0 7px;font-size:15px;line-height:1;color:var(--muted);border-top-left-radius:0;border-bottom-left-radius:0}
  .take:not(:disabled):hover{color:var(--wrong);border-color:var(--wrong)}
  .token.held .piece,.token.held .take{border-color:var(--accent);background:var(--accent-light)}
  .token.held:not(.dragging){box-shadow:0 0 0 2px var(--accent)}
  .token.dragging{z-index:2;scale:1.06;filter:drop-shadow(0 8px 14px var(--shadow))}
  .token.saving{opacity:.55}
  .ghost{display:inline-flex;align-items:baseline;padding:4px 10px;border:1px dashed var(--accent);border-radius:7px;font-size:22px;line-height:1.2;color:var(--accent);opacity:.75;
    font-family:var(--char-font)}
  footer{display:flex;flex-direction:column;gap:8px}
  footer :global(.character-search){width:100%;max-width:320px;min-width:0}
  .structure-error,.structure-notice{margin:0;font-size:12px;line-height:1.4;color:var(--wrong)}
  .structure-notice{color:var(--muted)}
  .said{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
  @media(max-width:700px){.structure-dialog{padding:16px}.piece,.ghost{font-size:19px;padding:3px 8px}}
  @media(prefers-reduced-motion:reduce){.tray{transition:none}.tray.landed{animation-duration:1ms}}
</style>
