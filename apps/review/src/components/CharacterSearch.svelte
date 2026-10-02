<script>
  import ScriptLegend from './ScriptLegend.svelte'
  // The search box and its candidate list: type what you see, pick the character. トモ is two code
  // points and one printed character (𪜈); ネ is a character the Meiji page prints as 𛄧. Choosing a
  // row is not a widening of a search — it is choosing which character to look at.
  //
  // Three things this box has to survive, because a Japanese keyboard does all three:
  // an IME composing とも (Enter commits the composition, it does not pick a candidate), a fast
  // typist whose earlier query answers after a later one, and a reader who clears the box and
  // expects the page behind it to come back.
  //
  // A page may give the box a `browse` snippet, shown while the box is empty and focused, and a
  // `token`: the filter that snippet chose, shown in the box until the reader removes it or types.
  // A page that knows its graphemes gives `groupOf`: the candidates a query names then show as the
  // same grapheme cards its browser shows, and the characters it holds none of fold away beneath them.
  import { onMount } from 'svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import ScriptText from './ScriptText.svelte'
  import GraphemeCard from './GraphemeCard.svelte'
  import { suggest, countsLabel, ownLabel } from '../lib/layers.js'
  import { t } from '../lib/i18n.svelte.js'

  let {
    value = $bindable(''),
    placeholder = t('search.placeholder'),
    label = t('search.label'),
    autofocus = false,
    onselect = () => {},
    oninput = () => {},
    onsubmit = null,
    compact = false,
    browse = null,
    token = '',
    tokenLabel = '',
    ontokenclear = () => {},
    groupOf = null,
    onchoosegroup = () => {},
    onform = () => {},
    // Rows name the code point rather than the reading.
    codePoints = false,
  } = $props()

  const listId = `candidates-${Math.random().toString(36).slice(2, 9)}`
  let root = $state(null), input = $state(null), open = $state(false), active = $state(-1), items = $state([])
  const PAGE = 8, CEILING = 48   // the service answers up to 48 candidates for one query
  let limit = $state(PAGE)
  let loading = $state(false), failed = $state(false), answer = $state(null)
  let options = $state([]), closed = false, timer, generation = 0
  let pending = null, composing = false, quiet = false, list = $state(null)

  // The graphemes the candidates belong to, once each and in the candidates' order, and the candidates
  // that belong to none the page holds.
  const cards = $derived.by(() => {
    if (!groupOf) return []
    const seen = new Map()
    for (const item of items) { const group = groupOf(item); if (group && !seen.has(group.key)) seen.set(group.key, group) }
    return [...seen.values()]
  })
  const others = $derived(groupOf ? items.map((item, index) => ({ item, index })).filter(({ item }) => !groupOf(item)) : [])
  const carded = $derived(cards.length > 0)

  function searchSignal() {
    pending?.abort()
    pending = new AbortController()
    return AbortSignal.any([pending.signal, AbortSignal.timeout(8000)])
  }

  function reset() {
    clearTimeout(timer)
    pending?.abort()
    generation += 1
    items = []; answer = null; active = -1; loading = false; failed = false; open = false
  }

  function seek(text) {
    clearTimeout(timer)
    pending?.abort()
    const current = ++generation
    // Old rows go as soon as the query changes: Enter during the wait must not pick a character the
    // reader has already typed past.
    items = []; active = -1; answer = null; failed = false
    if (!text.trim()) { loading = false; open = Boolean(browse) && document.activeElement === input; return }
    loading = true; open = true
    timer = setTimeout(async () => {
      try {
        const result = await suggest(text, limit, searchSignal())
        if (closed || current !== generation) return
        answer = result; items = result?.items ?? []; active = items.length ? 0 : -1
      } catch {
        if (!closed && current === generation) { failed = true; items = [] }
      } finally {
        if (!closed && current === generation) loading = false
      }
    }, 120)
  }

  function typed(text) {
    value = text
    limit = PAGE
    seek(text)
    oninput(text)
  }

  /** More of the same ranking: the reader is not asked to type more to see the rest. */
  async function more() {
    if (!value.trim() || loading || limit >= CEILING) return
    const current = generation
    const asked = value
    limit = Math.min(limit + PAGE, CEILING)
    loading = true
    try {
      const result = await suggest(asked, limit, searchSignal())
      // The answer only lands if it is still the query on screen.
      if (closed || current !== generation || value !== asked) return
      answer = result; items = result?.items ?? []
    } catch {
      if (!closed && current === generation) failed = true
    } finally {
      if (!closed && current === generation) loading = false
    }
  }

  function clear() {
    reset()
    limit = PAGE
    value = ''
    oninput('')
    input?.focus()
  }

  // Enter in the box opens the first candidate, with or without cards: トモ still opens 𪜈.
  function choose(index = active) {
    if (loading || index < 0) return
    const item = items[index]
    if (!item) return
    value = item.char
    open = false
    onselect(item)
  }

  function keys(event) {
    // An IME committing とも sends Enter with isComposing true (229 on older engines). That Enter
    // belongs to the composition, not to this list.
    if (event.isComposing || event.keyCode === 229) return
    if (event.key === 'Tab' && browsing) { open = false; return }
    if (event.key === 'ArrowDown' && browsing) {
      event.preventDefault()
      root.querySelector('.browse-panel button')?.focus()
      return
    }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && open && carded) {
      // The cards are buttons: the arrows walk into them, as they walk into the browse panel.
      event.preventDefault()
      const buttons = focusable()
      buttons[event.key === 'ArrowDown' ? 0 : buttons.length - 1]?.focus()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      // The first arrow opens a closed list, and the page must not scroll while it does.
      event.preventDefault()
      if (!open) { open = true; if (items.length && active < 0) active = 0; return }
      if (!items.length) return
      active = (active + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length
      return
    }
    if (event.key === 'Enter') {
      if (loading) return
      if (open && active >= 0 && items[active]) { event.preventDefault(); choose() }
      else if (onsubmit) { event.preventDefault(); onsubmit(value) }
      return
    }
    if (event.key === 'Escape') { if (open) { event.preventDefault(); open = false } }
    if (event.key === 'Backspace' && token && !value) { event.preventDefault(); ontokenclear() }
  }

  const browsing = $derived(open && Boolean(browse) && !value.trim())

  const focusable = () => [...(list?.querySelectorAll('button:not(:disabled), summary') ?? [])]
  /** Arrow keys among the cards' buttons; Escape, or an arrow past either end, returns to the box. */
  function walk(event) {
    if (event.key === 'Escape') { event.preventDefault(); done(); return }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const buttons = focusable(), at = buttons.indexOf(document.activeElement)
    const next = at + (event.key === 'ArrowDown' ? 1 : -1)
    if (next < 0 || next >= buttons.length) { quiet = true; input?.focus(); quiet = false; return }
    buttons[next].focus()
  }
  function chose(action, argument) { open = false; action(argument) }

  /** Closes the browse panel and puts focus back in the box without opening the panel again. */
  function done() {
    open = false
    quiet = true
    input?.focus()
    quiet = false
  }

  function blurred() {
    // A tap on a row has to land before the list closes, so the close waits one turn. Focus moving
    // into the list (a Tab into the browse panel) keeps it open.
    setTimeout(() => { if (!root?.contains(document.activeElement)) open = false }, 120)
  }

  // Keep the highlighted row visible when the arrow keys walk past the bottom of the list.
  $effect(() => {
    const index = active
    if (!open || index < 0) return
    options[index]?.scrollIntoView({ block: 'nearest' })
  })

  onMount(() => {
    if (autofocus) input?.focus()
    return () => { closed = true; clearTimeout(timer); pending?.abort() }
  })
</script>

{#snippet row(item, index)}
  <div class="candidate-row"><button type="button" class="candidate" id={`${listId}-${index}`} role={carded ? undefined : 'option'}
          bind:this={options[index]}
          aria-selected={carded ? undefined : index === active} class:active={!carded && index === active}
          onpointerenter={() => { if (!carded) active = index }} onclick={() => choose(index)}
          title={[item.name, item.reason].filter(Boolean).join(' · ')}>
    <ReferenceGlyph char={item.char} code_point={item.code_point} script={item.script} size="lg" />
    <span class="candidate-body">
      <span class="candidate-line">
        <b class="candidate-char"><ReferenceGlyph char={item.char} code_point={item.code_point} script={item.script} size="sm" /></b>
        <span class="candidate-reading">{#if item.reading && !codePoints}<ScriptText text={item.reading} />{:else}{item.code_point}{/if}</span>
        {#if item.kind === 'ligature'}<span class="tag">{t('search.ligature')}</span>{/if}
      </span>
      <span class="candidate-counts">{countsLabel(item.candidates) || ownLabel(item)}{#if item.grapheme?.character_count > 1}<span> · <ScriptText text={item.grapheme.label} /></span>{/if}</span>
    </span>
  </button></div>
{/snippet}

<div class="character-search" class:compact bind:this={root} onfocusout={blurred}>
  <form class="find" role="search" onsubmit={e => { e.preventDefault(); if (onsubmit) onsubmit(value); else choose() }}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>
    {#if token}<button type="button" class="find-token" aria-label={tokenLabel} onclick={() => { ontokenclear(); input?.focus() }}><ScriptText text={token} /> ×</button>{/if}
    <input bind:this={input} aria-label={label} bind:value placeholder={placeholder}
           autocomplete="off" spellcheck="false" role="combobox" aria-expanded={open}
           aria-controls={listId} aria-autocomplete="list" aria-busy={loading}
           aria-activedescendant={open && !carded && active >= 0 ? `${listId}-${active}` : undefined}
           oncompositionstart={() => { composing = true; reset() }}
           oncompositionend={e => { composing = false; typed(e.currentTarget.value) }}
           oninput={e => { if (!composing && !e.isComposing) typed(e.currentTarget.value) }}
           onfocus={() => { if (quiet) return; if (value.trim() && !items.length) seek(value); else if (items.length || browse) open = true }}
           onkeydown={keys} />
    {#if value}<button type="button" class="find-clear" aria-label={t('search.clear')} onclick={clear}>×</button>{/if}
  </form>

  {#if browsing}
    <!-- Pressing a button in the panel keeps focus in the box: Safari does not focus a clicked button,
         and the box's blur would close the panel before the click lands. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="candidate-list browse-panel" id={listId} onmousedown={e => e.preventDefault()}
         onkeydown={e => { if (e.key === 'Escape') { e.preventDefault(); done() } }}>{@render browse(done)}</div>
  {:else if open}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="candidate-list" class:carded id={listId} bind:this={list} role={carded ? 'group' : 'listbox'} aria-label={t('search.candidates.label')}
         onkeydown={e => { if (carded) walk(e) }}>
      {#if loading}<p class="candidate-status" role="status">{t('search.searching')}</p>{/if}
      {#if failed}<p class="candidate-status" role="alert">{t('search.failed')} <button type="button" onclick={() => seek(value)}>{t('common.tryAgain')}</button></p>{/if}
      {#if carded}
        {#each cards as group (group.key)}
          <div class="search-card"><GraphemeCard {group} onchoose={key => chose(onchoosegroup, key)} onform={form => chose(onform, form)} /></div>
        {/each}
        {#if others.length}
          <details class="candidate-others"><summary>{t('search.others', { count: others.length })}</summary>
            {#each others as { item, index } (item.code_point)}{@render row(item, index)}{/each}
          </details>
        {/if}
      {:else}
        {#each items as item, index (item.code_point)}{@render row(item, index)}{/each}
      {/if}
      {#if items.length && (!carded || others.length)}<div class="candidate-legend"><ScriptLegend /></div>{/if}
      {#if !loading && !failed && !items.length && answer}
        <p class="candidate-status">{answer.hint ?? t('search.noMatch')}</p>
      {/if}
      {#if answer?.more > 0 && limit < CEILING}
        <button type="button" class="candidate-more" onclick={more} disabled={loading}>
          {loading ? t('search.reading') : t('search.moreCandidates')}
        </button>
      {:else if answer?.more > 0}
        <p class="candidate-status">{t('search.narrowQuery')}</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .candidate-row{display:flex;align-items:center;gap:8px;padding-right:12px}
  .candidate-row .candidate{flex:1;min-width:0}
  .candidate-legend{padding:10px 14px;border-top:1px solid var(--line)}
  .candidate-list.carded{padding:8px}
  .search-card{padding:10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);font-size:12px}
  .search-card + .search-card{margin-top:8px}
  .candidate-others{margin-top:8px;color:var(--muted)}
  .candidate-others summary{cursor:pointer;padding:8px 6px;font-size:12px}
  .candidate-others .candidate-row{opacity:.8}
</style>
