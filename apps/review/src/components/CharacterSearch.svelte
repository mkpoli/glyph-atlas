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
  import { onMount } from 'svelte'
  import ZiLink from './ZiLink.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
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
  } = $props()

  const listId = `candidates-${Math.random().toString(36).slice(2, 9)}`
  let root = $state(null), input = $state(null), open = $state(false), active = $state(-1), items = $state([])
  const PAGE = 8, CEILING = 48   // the service answers up to 48 candidates for one query
  let limit = $state(PAGE)
  let loading = $state(false), failed = $state(false), answer = $state(null)
  let options = $state([]), closed = false, timer, generation = 0
  let pending = null, composing = false

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

<div class="character-search" class:compact bind:this={root} onfocusout={blurred}>
  <form class="find" role="search" onsubmit={e => { e.preventDefault(); if (onsubmit) onsubmit(value); else choose() }}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>
    {#if token}<button type="button" class="find-token" aria-label={tokenLabel} onclick={() => { ontokenclear(); input?.focus() }}><span lang="ja">{token}</span> ×</button>{/if}
    <input bind:this={input} aria-label={label} bind:value placeholder={placeholder}
           autocomplete="off" spellcheck="false" role="combobox" aria-expanded={open}
           aria-controls={listId} aria-autocomplete="list" aria-busy={loading}
           aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
           oncompositionstart={() => { composing = true; reset() }}
           oncompositionend={e => { composing = false; typed(e.currentTarget.value) }}
           oninput={e => { if (!composing && !e.isComposing) typed(e.currentTarget.value) }}
           onfocus={() => { if (value.trim() && !items.length) seek(value); else if (items.length || browse) open = true }}
           onkeydown={keys} />
    {#if value}<button type="button" class="find-clear" aria-label={t('search.clear')} onclick={clear}>×</button>{/if}
  </form>

  {#if open && browse && !value.trim()}
    <div class="candidate-list browse-panel">{@render browse(() => open = false)}</div>
  {:else if open}
    <div class="candidate-list" id={listId} role="listbox" aria-label={t('search.candidates.label')}>
      {#if loading}<p class="candidate-status" role="status">{t('search.searching')}</p>{/if}
      {#if failed}<p class="candidate-status" role="alert">{t('search.failed')} <button type="button" onclick={() => seek(value)}>{t('common.tryAgain')}</button></p>{/if}
      {#each items as item, index (item.code_point)}
        <div class="candidate-row"><button type="button" class="candidate" id={`${listId}-${index}`} role="option"
                bind:this={options[index]}
                aria-selected={index === active} class:active={index === active}
                onpointerenter={() => active = index} onclick={() => choose(index)}
                title={[item.name, item.reason].filter(Boolean).join(' · ')}>
          <ReferenceGlyph char={item.char} code_point={item.code_point} script={item.script} size="lg" />
          <span class="candidate-body">
            <span class="candidate-line">
              <b class="candidate-char"><ReferenceGlyph char={item.char} code_point={item.code_point} script={item.script} size="sm" /></b>
              <span class="candidate-reading">{item.reading ?? item.code_point}</span>
              {#if item.kind === 'ligature'}<span class="tag">{t('search.ligature')}</span>{/if}
            </span>
            <span class="candidate-counts">{countsLabel(item.candidates) || ownLabel(item)}{#if item.grapheme?.character_count > 1}<span> · {item.grapheme.label}</span>{/if}</span>
          </span>
        </button><ZiLink character={item.char} compact /></div>
      {/each}
      {#if items.length}<div class="candidate-legend"><ScriptLegend /></div>{/if}
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
</style>
