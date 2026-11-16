<script>
  // The 構形 of a character's forms, laid out to arrange: each 構形 is a tray holding the forms built
  // so, and a signed-in reader moves a form to another tray (has_structure), so the form stands under
  // that 構形 for every crop. A form is moved by dragging it, by picking it up with a click and
  // clicking a tray, or from the keyboard: Enter picks it up, the arrows aim at a tray, Enter puts it
  // down and Escape lets go. A form the reader filed carries a × that takes the filing back; putting
  // it back on the tray its description gives does the same, and so does the strip that shows at the
  // end of the board while the form is held. A tray stays on the board once shown, emptied or not,
  // and a search adds the 構形 of any other character to it.
  import { onMount, tick, untrack } from 'svelte'
  import { flip } from 'svelte/animate'
  import { crossfade } from 'svelte/transition'
  import { cubicOut } from 'svelte/easing'
  import FormText from './FormText.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import { setStructure } from '../lib/cropForms.js'
  import { composable } from '../lib/structures.js'
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
  let trays = $state([]), held = $state(null), over = $state(null), dragging = $state(false)
  let busy = $state(null), landed = $state(null), error = $state(''), said = $state('')
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
    const next = [...trays]
    for (const form of listed) {
      const stands = found.get(form.char)
      if (!stands) continue
      let tray = next.find(tray => tray.id === stands.id)
      if (!tray) next.push(tray = { id: stands.id, label: stands.label, rank: 0, target: { structure: stands.id } })
      if (stands.by) continue
      home.set(form.char, stands.id)
      const [rank, target] = naming(form)
      if (rank > tray.rank) Object.assign(tray, { rank, target })
    }
    known = found; trays = next
  }
  const placed = $derived(trays.map(tray => ({ ...tray, forms: listed.filter(form => known.get(form.char)?.id === tray.id) })))
  const loose = $derived(listed.filter(form => !known.get(form.char)))
  // The 構形 the reader's own filing of a form names: another reader's may hold the form elsewhere.
  const mineOf = char => {
    const stands = known.get(char)
    if (!me || !stands) return null
    return stands.by?.includes(me) ? stands.id : (stands.others ?? []).find(other => other.by.includes(me))?.id ?? null
  }
  // Where the held form can be put: every tray but the one it stands in, and out of the reader's own
  // filing when it has one.
  const OUT = 'out'
  const out = $derived(held && mineOf(held) ? t('structure.take', { form: held }) : '')
  const targets = $derived(held ? [...trays.filter(tray => tray.id !== known.get(held)?.id).map(tray => tray.id), ...(out ? [OUT] : [])] : [])
  // A form on its way out of a tray is inert while it leaves; the one that stands is the other.
  const chipOf = char => board?.querySelector(`.token:not([inert]) > [data-form="${CSS.escape(char)}"]`)
  const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches
  const [send, receive] = crossfade({ duration: () => calm() ? 0 : 280, easing: cubicOut })
  const slide = { duration: () => calm() ? 0 : 220, easing: cubicOut }

  let wrote = false
  // A move shows at once and is read again once saved; a save the site refuses puts the form back.
  async function write(char, stands, save) {
    const before = known
    busy = char; error = ''
    if (stands !== undefined) { known = new Map([...known, [char, stands]]); landed = stands.id }
    try {
      await save()
      wrote = true
      try { absorb(await read([char, ...listed.map(form => form.char)], known)) } catch (failure) { error = failure.message }
    } catch (failure) { known = before; error = failure.message }
    busy = null
    await tick()
    chipOf(char)?.focus({ preventScroll: true })
  }
  function place(char, tray) {
    if (known.get(char)?.id === tray.id) return
    if (mineOf(char) && home.get(char) === tray.id) return takeOut(char)
    return write(char, { id: tray.id, label: tray.label, by: [me], others: [] }, () => setStructure(char, tray.target))
  }
  function takeOut(char) {
    const mine = mineOf(char), back = trays.find(tray => tray.id === home.get(char))
    if (!mine || busy) return
    release()
    return write(char, back ? { id: back.id, label: back.label, by: null, others: [] } : undefined, () => setStructure(char, { structure: mine }, { remove: true }))
  }
  function release() { held = null; over = null; dragging = false }
  function pick(char) {
    if (busy || !me) return
    if (held === char) return release()
    held = char; over = null; dragging = false
  }
  function drop(to) {
    const char = held, tray = trays.find(tray => tray.id === to)
    if (char && to === OUT) return takeOut(char)
    release()
    if (char && tray) place(char, tray)
  }

  // A drag with a mouse or a pen; a touch picks the form up with a tap, so the board still scrolls.
  let grip = null, skipClick = false
  function trayAt(x, y) {
    const under = document.elementsFromPoint(x, y).find(element => !grip.node.contains(element))
    const to = under?.closest('[data-tray]')?.dataset.tray
    return targets.includes(to) ? to : null
  }
  function down(event, char) {
    if (busy || !me || event.button !== 0 || !event.isPrimary || event.pointerType === 'touch') return
    grip = { pointer: event.pointerId, x: event.clientX, y: event.clientY, node: event.currentTarget.closest('.token'), char, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function move(event) {
    if (!grip || event.pointerId !== grip.pointer) return
    const dx = event.clientX - grip.x, dy = event.clientY - grip.y
    if (!grip.moved) {
      if (Math.hypot(dx, dy) < 6) return
      grip.moved = true; held = grip.char; dragging = true
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
    // A form put down leaves from where it was let go; one let go elsewhere goes back.
    if (over) drop(over)
    else { settle(node); release() }
  }
  function lost(event) {
    if (!grip || event.pointerId !== grip.pointer) return
    const { node, moved } = grip
    grip = null
    if (moved) { settle(node); release() }
  }
  function tap(event, char) {
    if (skipClick) { skipClick = false; event.stopPropagation(); return }
    // With another form held, the click is the tray's: the held form is put down there.
    if (held && held !== char) return
    event.stopPropagation()
    pick(char)
  }
  let spaced = false
  function keys(event, char) {
    if (held !== char) return
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    if (step) {
      event.preventDefault()
      const at = targets.indexOf(over)
      over = targets[at < 0 ? (step > 0 ? 0 : targets.length - 1) : (at + step + targets.length) % targets.length] ?? null
      const tray = trays.find(tray => tray.id === over)
      if (over === OUT) said = out
      if (tray) { said = `${tray.label} · ${t('structure.place', { form: char })}`; board.querySelector(`[data-tray="${CSS.escape(over)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }
    } else if ((event.key === 'Enter' || event.key === ' ') && over) {
      event.preventDefault()
      spaced = event.key === ' '
      drop(over)
    } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); release() }
  }
  // Space activates a button as the key comes up, which would pick the form up again where it landed.
  function keyup(event) { if (event.key === ' ' && spaced) { spaced = false; event.preventDefault() } }

  async function add(item) {
    query = ''
    if (!composable(item.char)) return
    if (!listed.some(form => form.char === item.char)) {
      added = [...added, { char: item.char, script: item.script ?? '', lang: item.lang ?? '' }]
      try { absorb(await read([item.char], known)) } catch (failure) { error = failure.message }
    }
    await tick()
    const chip = chipOf(item.char)
    chip?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    chip?.focus({ preventScroll: true })
  }
  const titleOf = form => form.regions ? `${form.char} ${form.regions}` : form.char
  function cancel(event) { if (held) { event.preventDefault(); release() } }
  function closed() { if (wrote) changed(); close() }
  onMount(() => {
    absorb(opened)
    dialog.showModal()
    // `showModal` focuses the close button; the work starts at the form the reader came from.
    if (current) tick().then(() => chipOf(current)?.focus({ preventScroll: true }))
    read(listed.map(form => form.char), opened).then(absorb, failure => { error = failure.message })
  })
</script>

{#snippet token(form)}
  {@const mine = Boolean(mineOf(form.char))}
  <button type="button" class="piece" class:current={form.char === current} data-form={form.char} aria-pressed={held === form.char} aria-busy={busy === form.char}
    aria-disabled={!me || Boolean(busy)} title={titleOf(form)} aria-label={titleOf(form)}
    onpointerdown={event => down(event, form.char)} onpointermove={move} onpointerup={up} onpointercancel={lost} onlostpointercapture={lost}
    onclick={event => tap(event, form.char)} onkeydown={event => keys(event, form.char)} onkeyup={keyup}>
    <FormText text={form.char} script={form.script} lang={form.lang} />
  </button>{#if mine}<button type="button" class="take" disabled={Boolean(busy)} aria-label={t('structure.take', { form: form.char })} title={t('structure.take', { form: form.char })}
    onclick={event => { event.stopPropagation(); takeOut(form.char) }}>×</button>{/if}
{/snippet}

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog class="structure-dialog" bind:this={dialog} aria-labelledby="{id}-title" oncancel={cancel} onclose={closed} onclick={event => { if (event.target === dialog) dialog.close() }}>
  <header>
    <h2 class="overline" id="{id}-title">{t('structure.arrange')}</h2>
    <button type="button" class="icon-button" aria-label={t('structure.close')} onclick={() => dialog.close()}>×</button>
  </header>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="board" class:holding={held} class:dragging bind:this={board} onclick={() => { if (held) release() }}>
    {#each placed as tray (tray.id)}
      <section class="tray" class:target={targets.includes(tray.id)} class:over={over === tray.id} class:landed={landed === tray.id} onanimationend={() => { if (landed === tray.id) landed = null }} data-tray={tray.id} animate:flip={slide}
        aria-label={held && targets.includes(tray.id) ? `${tray.label} · ${t('structure.place', { form: held })}` : tray.label}
        onclick={event => { if (held && targets.includes(tray.id)) { event.stopPropagation(); drop(tray.id) } }}>
        <h3 lang="zh">{tray.label}</h3>
        <div class="tray-forms">
          {#each tray.forms as form (form.char)}
            <span class="token" class:mine={Boolean(mineOf(form.char))} class:held={held === form.char} class:dragging={dragging && held === form.char} class:saving={busy === form.char}
              in:receive={{ key: form.char }} out:send={{ key: form.char }} animate:flip={slide}>{@render token(form)}</span>
          {/each}
          {#if over === tray.id && held}<span class="ghost" aria-hidden="true"><FormText text={held} /></span>{/if}
        </div>
      </section>
    {/each}
    {#if loose.length}
      <section class="tray loose">
        <div class="tray-forms">
          {#each loose as form (form.char)}
            <span class="token" class:held={held === form.char} class:dragging={dragging && held === form.char} class:saving={busy === form.char}
              in:receive={{ key: form.char }} out:send={{ key: form.char }} animate:flip={slide}>{@render token(form)}</span>
          {/each}
        </div>
      </section>
    {/if}
    {#if out}
      <button type="button" class="tray out target" class:over={over === OUT} data-tray={OUT} onclick={event => { event.stopPropagation(); drop(OUT) }}>{out}</button>
    {/if}
  </div>
  <footer>
    <CharacterSearch compact codePoints bind:value={query} label={t('form.add')} placeholder={t('search.placeholder')} onselect={add} />
    {#if error}<p class="structure-error" role="alert">{error}</p>{/if}
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
  .tray h3{margin:0;font-size:15px;font-weight:400;line-height:1.3}
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
  .piece{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;font-size:22px;line-height:1.2;cursor:grab;user-select:none;-webkit-user-select:none;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans","Noto Serif Tangut",serif}
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
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans","Noto Serif Tangut",serif}
  footer{display:flex;flex-direction:column;gap:8px}
  footer :global(.character-search){width:100%;max-width:320px;min-width:0}
  .structure-error{margin:0;font-size:12px;line-height:1.4;color:var(--wrong)}
  .said{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
  @media(max-width:700px){.structure-dialog{padding:16px}.piece,.ghost{font-size:19px;padding:3px 8px}}
  @media(prefers-reduced-motion:reduce){.tray{transition:none}.tray.landed{animation-duration:1ms}}
</style>
