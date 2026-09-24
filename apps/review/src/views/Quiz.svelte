<script>
  import ProductionBadge from '../components/ProductionBadge.svelte'
  // Select problems, then finish each crop's issue and optional correction before moving on.
  // Only explicitly marked problems are saved as reviews. An unmarked crop that was on screen is
  // recorded as seen, which keeps it out of later rounds without confirming it.
  import { onMount, tick } from 'svelte'
  import Glyph from '../components/Glyph.svelte'
  import IssuePicker from '../components/IssuePicker.svelte'
  import ReadingSuggestions from '../components/ReadingSuggestions.svelte'
  import QuizFocus from '../components/QuizFocus.svelte'
  import { catalogue, randomSeed, request, remember, stored, number, suggestionsFor } from '../lib/client.js'
  import { issues, issueTitle, suggestsReading, isSingle, isSkip, greetSuggestions, SKIP_LABEL } from '../lib/issues.js'
  import { nextCharacter, ROUND_BATCH } from '../lib/reviewRounds.js'
  let { clientId, initialReading = '', inspect } = $props()
  let data = $state(null), items = $state([]), choices = $state({}), selected = $state({})
  let loaded = $state({}), failed = $state({}), suggestions = $state({}), contextSuggestions = $state({})
  // Crops the reader declined to judge: no choice, no request, not counted (see `skip`).
  let skipped = $state({})
  // Crops at least half of which have been on screen this round. Only these can be recorded as seen:
  // a batch that loaded below the fold was never looked at, and passing it would drop it for good.
  let viewed = $state({})
  let reading = $state(''), loading = $state(true), saving = $state(false), error = $state('')
  let categoryOpen = $state(false), search = $state(''), completed = $state(0), last = $state(null)
  let roundId = $state(crypto.randomUUID()), requestId = 0, closed = false
  let roundSeed = randomSeed()
  let history = $state([]), historyIndex = $state(-1)
  let loadingMore = $state(false), hasMore = $state(false)
  // True while the load-more row is on screen or close to it: scrolling down loads the next batch.
  let nearEnd = $state(false)
  function watchSeen(node, id) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.intersectionRatio >= 0.5)) { viewed = { ...viewed, [id]: true }; observer.disconnect() }
    }, { threshold: 0.5 })
    observer.observe(node)
    return { destroy() { observer.disconnect() } }
  }
  function watchEnd(node) {
    const observer = new IntersectionObserver(entries => { nearEnd = entries.some(entry => entry.isIntersecting) },
      { rootMargin: '0px 0px 600px 0px' })
    observer.observe(node)
    return { destroy() { observer.disconnect(); nearEnd = false } }
  }
  $effect(() => {
    // Never while an error is showing: a failed batch would otherwise be retried at once, forever,
    // and an error from another action would be cleared before anyone could read it.
    if (nearEnd && hasMore && !error && !loading && !saving && !loadingMore && items.length < roundLimit) loadMore()
  })
  const roundLimit = $derived(data?.review_limit ?? 4096)
  let production = $state('non-movable-type')
  // The workflow state: which step, and where in the selected crops the reader is.
  let step = $state('select'), at = $state(0)
  let suggestionsElement = $state(null)
  const keys = 'qwertyasdfgh'.split('')
  const selection = $derived(Object.keys(selected).filter(id => selected[id]))
  const selectedItems = $derived(selection.map(id => items.find(i => i.id === id))
    .filter(item => item && !failed[item.id]))
  const queue = $derived(selectedItems)
  // Skips and issue changes shrink the queue, so the position that is *shown* is always clamped:
  // `at` may still point past the end for a moment, and a stage must never show a blank crop.
  const focusIndex = $derived(Math.min(at, Math.max(queue.length - 1, 0)))
  const current = $derived(step === 'select' ? null : queue[focusIndex] ?? null)
  const answered = $derived(selectedItems.every(i => choices[i.id] || skipped[i.id]))
  const decided = $derived(remaining.filter(i => choices[i.id]?.verdict === 'wrong').length)
  // A selected crop still needs an explicit issue before it can be saved.
  const undecided = $derived(remaining.filter(i => selected[i.id] && !choices[i.id] && !skipped[i.id]).length)
  // Unavailable and skipped images cannot carry a decision.
  const remaining = $derived(items.filter(i => loaded[i.id] && !failed[i.id] && !skipped[i.id]))
  const settled = $derived(items.every(i => skipped[i.id] || failed[i.id] || (loaded[i.id] && !failed[i.id])))
  const ready = $derived(items.length > 0 && settled && remaining.length > 0)
  const exhausted = $derived(items.length > 0 && settled && !remaining.length)
  const categories = $derived((data?.categories ?? []).filter(c => c.due > 0 && c.label.includes(search)))

  const canNext = $derived((data?.categories ?? []).some(c => c.due > 0 && c.label !== reading))
  function snapshot() {
    return $state.snapshot({ reading, items, choices, selected, skipped, suggestions, contextSuggestions,
      roundId, roundSeed, hasMore, production, summary: data })
  }
  function checkpoint() {
    if (historyIndex >= 0) history[historyIndex] = snapshot()
  }
  function restoreRound(round) {
    production = round.production; data = round.summary
    reading = round.reading; items = round.items; choices = round.choices; selected = round.selected
    skipped = round.skipped; suggestions = round.suggestions; contextSuggestions = round.contextSuggestions
    roundId = round.roundId; roundSeed = round.roundSeed; hasMore = round.hasMore
    loaded = {}; failed = {}; viewed = {}; step = 'select'; at = 0; error = ''; categoryOpen = false
  }
  function visit(index) {
    if (saving || loading || index < 0 || index >= history.length || index === historyIndex) return
    checkpoint()
    ++requestId; loadingMore = false
    historyIndex = index
    restoreRound($state.snapshot(history[index]))
  }
  function chooseCategory(target) {
    categoryOpen = false
    if (target === reading) return
    const previous = history.findLastIndex(round => round.reading === target && round.production === production)
    if (previous >= 0) visit(previous)
    else load({ target })
  }
  async function load({ target = null, replace = false, scope = production } = {}) {
    checkpoint()
    const id = ++requestId
    loading = true; loadingMore = false; error = ''; categoryOpen = false
    try {
      const summary = await catalogue({ purpose: 'review', production: scope, limit: 1, state: 'due' })
      if (closed || id !== requestId) return
      data = summary
      const epochKey = 'atlas.review-epoch.' + clientId
      if (stored(epochKey, null) !== (summary.review_epoch ?? null)) {
        last = null; completed = 0; history = []; historyIndex = -1
        remember('atlas.last-round.' + clientId, null)
        remember(epochKey, summary.review_epoch ?? null)
      }
      const available = summary.categories.filter(c => c.due > 0)
      const chosen = target && available.some(c => c.label === target) ? target
        : nextCharacter(available, scope === production ? reading : '', history.filter(r => r.production === scope), randomSeed())
      if (!chosen) {
        if (scope !== production) {
          restoreRound({ reading: '', items: [], choices: {}, selected: {}, skipped: {}, suggestions: {},
            contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: randomSeed(), hasMore: false,
            production: scope, summary })
          historyIndex = -1
        } else if (!reading) items = []
        else error = 'No other characters are ready. You can load more of this character or go back.'
        return
      }
      const seed = randomSeed()
      const result = await catalogue({ purpose: 'review', production: scope, reading: chosen, state: 'due', limit: ROUND_BATCH, seed })
      if (closed || id !== requestId) return
      restoreRound({ reading: chosen, items: result.items, choices: {}, selected: {}, skipped: {},
        suggestions: {}, contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: seed,
        hasMore: result.total > result.items.length, production: scope, summary })
      if (replace && historyIndex >= 0) history[historyIndex] = snapshot()
      else {
        history = [...history.slice(0, historyIndex + 1), snapshot(), ...history.slice(historyIndex + 1)]
        historyIndex += 1
      }
    } catch (e) { if (!closed && id === requestId) error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  async function loadMore() {
    if (loading || saving || loadingMore || !hasMore || items.length >= roundLimit) return
    const id = requestId, round = roundId
    loadingMore = true; error = ''
    const seen = new Set(items.map(item => item.id))
    let offset = 0, additions = [], more = false
    try {
      // Re-read this character with a stable shuffle: concurrent reviews may have removed rows.
      // Deduplicate by occurrence instead of treating an old offset as a permanent position.
      const batchLimit = Math.min(ROUND_BATCH, roundLimit - items.length)
      while (additions.length < batchLimit) {
        const result = await catalogue({ purpose: 'review', production, reading, state: 'due', limit: 96, offset, seed: roundSeed })
        if (closed || id !== requestId || round !== roundId) return
        const fresh = result.items.filter(item => !seen.has(item.id))
        const room = batchLimit - additions.length
        const batch = fresh.slice(0, room)
        additions.push(...batch)
        batch.forEach(item => seen.add(item.id))
        offset += result.items.length
        if (additions.length >= batchLimit) {
          more = fresh.length > room || offset < result.total
          break
        }
        if (!result.items.length || offset >= result.total) { more = false; break }
      }
      items = [...items, ...additions]; hasMore = more
    } catch (e) { if (id === requestId) error = e.message }
    finally { if (id === requestId) loadingMore = false }
  }
  const decidable = $derived(remaining)
  const available = $derived(decidable.length)

  // Step one: choose the crops that are wrong. A selection is not a verdict, so nothing is assigned
  // here, and a crop taken out of the selection gives up the answer it had.
  function toggle(id) {
    if (saving || loading || !loaded[id] || skipped[id] || failed[id]) return
    if (selected[id]) {
      selected = { ...selected, [id]: false }
      if (choices[id]) choices = without(choices, [id])
    } else {
      selected = { ...selected, [id]: true }
    }
    at = 0
  }
  function selectAll() {
    const open = decidable.map(i => i.id)
    const all = open.length && open.every(id => selected[id])
    if (all) { clearSelection(); return }
    selected = Object.fromEntries(open.map(id => [id, true]))
    at = 0
  }
  /**
   * Take back the selection, and the answers that belonged to it.
   *
   * Deselecting a crop withdraws its proposed issue without confirming it.
   */
  function clearSelection() {
    choices = without(choices, selection)
    selected = {}; at = 0
  }
  function without(map, ids) {
    const next = { ...map }
    for (const id of ids) delete next[id]
    return next
  }
  /**
   * The answer a crop keeps when its issue changes.
   *
   * The same issue keeps whatever was chosen for it. A different issue keeps a correction only when
   * that correction fits the new issue — one character for a wrong character, more than one for joined
   * characters — because a two-character answer on a single-character issue is a payload the round
   * refuses. Everything else is cleared, and the character layer is never left holding it.
   */
  function compatible(choice, issue, previous) {
    if (issue === previous || (issue === 'reading' && previous === 'character')) return choice
    const correction = choice?.character || choice?.correction
    if (!correction) return { correction: null, character: null }
    if (!suggestsReading(issue)) return { correction: null, character: null }
    const fits = issue === 'merged' ? !isSingle(correction) : isSingle(correction)
    return fits ? { correction, character: null } : { correction: null, character: null }
  }
  /** The answer for one crop, from step two: its own issue, never a shared one. */
  function assign(id, issue) {
    if (saving) return
    if (isSkip(issue)) { skip([id]); return }
    const previous = choices[id]?.issue ?? null
    const kept = compatible(choices[id], issue, previous)
    choices = { ...choices, [id]: { verdict: 'wrong', issue, ...kept } }
    if (suggestsReading(issue)) suggest(items.find(i => i.id === id))
    error = ''
  }
  async function assignCurrent(issue) {
    if (!current) return
    const id = current.id
    assign(current.id, issue)
    if (!isSkip(issue)) step = suggestsReading(issue) ? 'correct' : 'issue'
    if (step === 'correct') {
      await tick()
      if (current?.id === id) greetSuggestions(suggestionsElement, { focus: true })
    }
  }
  async function suggest(item) {
    const round = roundId
    const keep = (source, result) => {
      if (closed) return
      if (roundId === round) {
        if (source === 'context') contextSuggestions = { ...contextSuggestions, [item.id]: result }
        else suggestions = { ...suggestions, [item.id]: result }
      } else {
        const index = history.findIndex(entry => entry.roundId === round)
        const field = source === 'context' ? 'contextSuggestions' : 'suggestions'
        if (index >= 0) history[index][field] = { ...history[index][field], [item.id]: result }
      }
    }
    // Settle each source independently so the faster answer is usable at once.
    suggestionsFor(item, 'context').then(result => keep('context', result))
    const result = await suggestionsFor(item)
    keep('visual', result)
  }
  /** Take a skipped crop back into the round: nobody has decided about it yet. */
  function restore(id) {
    if (saving) return
    skipped = without(skipped, [id])
  }
  /**
   * Decline to judge these crops: no choice, no review, no request.
   *
   * A skip is not a verdict — it is not written, not counted, and the crop stays pending in the
   * collection for a later round.
   */
  function skip(ids = selection) {
    if (saving || loading) return
    const wanted = ids.filter(id => items.some(i => i.id === id))
    if (!wanted.length) return
    const removedCurrent = current && wanted.includes(current.id)
    skipped = { ...skipped, ...Object.fromEntries(wanted.map(id => [id, true])) }
    choices = without(choices, wanted)
    selected = without(selected, wanted)
    error = ''
    if (removedCurrent) jump(Math.min(at, Math.max(selectedItems.length - 1, 0)))
  }
  function inspectChoice(item) {
    inspect(item.id, value => {
      if (!value) return
      if (value.skip) { skip([item.id]); return }
      if (skipped[item.id] || failed[item.id]) return
      if (value.unselect || value.verdict === 'match') {
        choices = without(choices, [item.id])
        selected = { ...selected, [item.id]: false }
        return
      }
      choices = { ...choices, [item.id]: value }
      selected = { ...selected, [item.id]: true }
      if (suggestsReading(value.issue)) suggest(item)
    })
  }
  function chooseSuggestion(id, value, noneSelected = false) {
    const current = choices[id]
    if (!current) return
    choices = { ...choices, [id]: value && isSingle(value)
      ? { verdict: 'wrong', issue: 'character', character: value, correction: null, noneSelected: false }
      : { ...current, issue: current.issue === 'character' ? 'reading' : current.issue,
          character: null, correction: value, noneSelected } }
  }
  function reviewSelected() {
    // No `answered` guard here: the reader has just selected the crops and has not been asked for a
    // problem yet, so this is the way in. The guard belongs to the step that leaves the issue stage.
    if (!selection.length) return
    jump(0)
  }
  function back() {
    step = step === 'correct' ? 'issue' : 'select'
    at = Math.min(at, Math.max(queue.length - 1, 0))
    error = ''
  }
  function jump(i) {
    at = Math.max(0, Math.min(queue.length - 1, i))
    const item = queue[at], choice = choices[item?.id]
    step = suggestsReading(choice?.issue) ? 'correct' : 'issue'
    if (item && step === 'correct' && (!suggestions[item.id] || !contextSuggestions[item.id])) suggest(item)
    error = ''
  }
  const move = delta => jump(focusIndex + delta)

  /** The action the primary button offers, and the one Ctrl/Cmd+Enter performs. */
  function primary() {
    if (saving || loading || loadingMore) return
    if (!selection.length) { pass(); return }
    if (step === 'select') { reviewSelected(); return }
    if (!current || !choices[current.id]) return
    if (focusIndex < queue.length - 1) { move(1); return }
    if (!answered) { jump(selectedItems.findIndex(i => !choices[i.id])); return }
    submit()
  }
  async function submit() {
    if (saving || loadingMore || !ready) return
    if (step === 'select') return
    if (selection.length && !answered) { error = 'Give each selected crop its own problem first.'; return }
    // Build an atomic request from explicit problems only; UI choice state never enters the API.
    const marked = new Set(selection)
    const answers = remaining.filter(i => marked.has(i.id) && choices[i.id]?.verdict === 'wrong').map(i => {
      const { character, noneSelected, ...rest } = choices[i.id]
      return { id: i.id, revision: i.revision, image_sha256: i.image_sha256, ...rest,
               ...(character ? { character } : {}) }
    })
    // Every other crop the round showed was seen and left unflagged. That is not a confirmation,
    // but it is recorded, so the crop is not dealt again.
    const flagged = new Set(answers.map(answer => answer.id))
    const seen = remaining.filter(i => !flagged.has(i.id) && viewed[i.id]).map(i => ({ id: i.id, image_sha256: i.image_sha256, image: i.image }))
    if (!answers.length && !seen.length) { await load(); return }
    saving = true; error = ''
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, answers, seen })
      last = { id: roundId, count: answers.length, label: reading, production }
      remember('atlas.last-round.' + clientId, last); completed += answers.length
      const savedIds = new Set([...answers, ...seen].map(answer => answer.id))
      items = items.filter(item => !savedIds.has(item.id))
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      await load()
    } catch (e) { error = e.status === 409 ? 'This round changed. Your choices are kept. Reload the round to continue.' : e.message }
    finally { saving = false }
  }
  // Moving on from a round with nothing flagged: the crops it showed were seen, so they are recorded
  // as seen before the next round is dealt. Crops that failed to load or were skipped were not seen.
  async function pass() {
    if (saving || loading || loadingMore) return
    const seen = remaining.filter(i => viewed[i.id]).map(i => ({ id: i.id, image_sha256: i.image_sha256, image: i.image }))
    if (!seen.length || selection.length) { await load(); return }
    saving = true; error = ''
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, seen })
      last = { id: roundId, count: 0, label: reading, production }
      remember('atlas.last-round.' + clientId, last)
      const seenIds = new Set(seen.map(crop => crop.id))
      items = items.filter(item => !seenIds.has(item.id))
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      await load()
    } catch (e) { error = e.message }
    finally { saving = false }
  }
  async function undo() {
    if (!last || saving) return
    saving = true; error = ''
    try {
      await request(`/atlas/rounds/${last.id}/undo`, { client_id: clientId })
      const target = last.label, scope = last.production ?? production
      completed = Math.max(0, completed - last.count); last = null
      remember('atlas.last-round.' + clientId, null); await load({ target, scope })
    } catch (e) { error = e.message }
    finally { saving = false }
  }
  function keydown(e) {
    if (document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
    // The save is explicit: Ctrl/Cmd+Enter is the shortcut, and a bare Enter belongs to whatever
    // control has focus — choosing a crop or a suggestion must never post the round.
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); primary(); return }
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const control = e.target.closest('button, a, input, textarea, select, summary, [contenteditable="true"]')
    if (step === 'select') {
      const index = keys.indexOf(e.key.toLowerCase())
      if (index >= 0 && items[index] && !control) { e.preventDefault(); toggle(items[index].id) }
      if (e.key === 'Escape') clearSelection()
      return
    }
    if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1); return }
    if (e.key === 'ArrowRight') { e.preventDefault(); move(1); return }
    if (e.key === 'Escape') { e.preventDefault(); back(); return }
    if (step === 'issue' && /^[1-5]$/.test(e.key)) { e.preventDefault(); assignCurrent(issues[Number(e.key) - 1].id) }
    if (step === 'correct' && e.key.toLowerCase() === 'n' && !control) { e.preventDefault(); if (current) chooseSuggestion(current.id, null, true) }
  }
  onMount(() => { last = stored('atlas.last-round.' + clientId, null); load({ target: initialReading || null }); return () => { closed = true } })
</script>

<svelte:window onkeydown={keydown} />
<section class="quiz-workspace">
  <div class="quiz-topline"><a href="#/" class="quiet-link">← Collection</a><div class="round-count"><span class="live-dot"></span>{number(completed)} issues saved this session</div>{#if last}<button class="undo-round" disabled={saving} onclick={undo}>↶ Undo last round</button>{/if}</div>
  <div class="quiz-heading"><div class="target-character" aria-label={`Target reading ${reading}`}>{reading || '字'}</div><div class="quiz-title"><p class="overline">QUICK REVIEW · {step === 'select' ? 'SELECT' : 'REVIEW'}</p><h1>{step === 'select' ? 'Which crops need fixing?' : step === 'issue' ? 'What’s wrong with this crop?' : choices[current?.id]?.issue === 'merged' ? 'What’s in this crop?' : 'Which character is this?'}</h1>{#if step === 'select'}<p>One complete <b>{reading || "…"}</b> per crop. Select extra characters, bad cuts, or a different character.</p>{/if}</div><div class="round-switch"><button class="category-toggle" disabled={saving || loading} onclick={() => categoryOpen = !categoryOpen}>Change character ⌄</button><button class="quiet-link" disabled={saving || loading || !canNext} onclick={() => load()}>Next character →</button></div></div>
  {#if categoryOpen}<div class="round-categories"><input aria-label="Find a category" placeholder="Find a reading…" bind:value={search}/><div class="category-options">{#each categories as c}<button disabled={saving} onclick={() => chooseCategory(c.label)}><span>{c.label}</span><small>{c.due}</small></button>{/each}</div></div>{/if}
  <label class="review-material">Material
    <select aria-label="Review material" value={production} disabled={saving || loading || loadingMore}
      onchange={event => load({ scope: event.currentTarget.value, target: reading })}>
      <option value="non-movable-type">Exclude movable type</option>
      <option value="manuscript">Handwritten</option>
      <option value="woodblock">Woodblock</option>
      <option value="movable-type">Movable type</option>
      <option value="mixed">Mixed</option>
      <option value="unknown">Not classified</option>
      <option value="all">All materials</option>
    </select>
  </label>
  {#if history.length > 1}
    <nav class="character-history" aria-label="Characters visited">
      <button class="previous-reading" aria-label="Previous character round" disabled={saving || loading || historyIndex <= 0} onclick={() => visit(historyIndex - 1)}>← Previous</button>
      <div class="history-characters">{#each history as round, i (round.roundId)}<button class="history-character" class:current={i === historyIndex} aria-current={i === historyIndex ? 'step' : undefined} aria-label={`Return to ${round.reading}`} disabled={saving || loading} onclick={() => visit(i)}>{round.reading}</button>{/each}</div>
      <button class="forward-reading" aria-label="Next visited character round" disabled={saving || loading || historyIndex >= history.length - 1} onclick={() => visit(historyIndex + 1)}>→</button>
    </nav>
  {/if}
  {#if error}<div class="error-message" role="alert"><span>{error}</span>{#if error.includes('changed')}<button disabled={saving} onclick={() => load({ target: reading, replace: true })}>Reload round</button>{/if}</div>{/if}

  {#if step === 'select'}
    <div class="stage-toolbar"><strong>{selection.length ? `${selection.length} selected` : 'Select the problems'}</strong><button class="bulk-toggle" disabled={saving || loading || !available} onclick={selectAll}>{selection.length === decidable.length && selection.length ? 'None' : 'All'}</button><button class="bulk-toggle skip-selection" disabled={saving || loading || !selection.length} onclick={() => skip()}>{SKIP_LABEL} selected</button><span class="keyboard-hint">qwerty… picks a crop</span></div>
    {#key roundId}<div class="quiz-grid" aria-label="Review round" aria-busy={loading}>
      {#if loading}{#each Array(12) as _}<div class="quiz-skeleton"></div>{/each}
      {:else}{#each items as item, i (item.id)}
        <div class="quiz-tile" use:watchSeen={item.id} data-unit={item.id} class:selected={selected[item.id]} class:wrong={choices[item.id]?.verdict === 'wrong'} class:unavailable={failed[item.id]} class:skipped={skipped[item.id]}>
          <button class="quiz-choice" aria-label={`Select character ${i + 1}`} aria-pressed={!!selected[item.id]} disabled={saving || !loaded[item.id] || skipped[item.id]} onclick={() => toggle(item.id)}><Glyph {item} eager onload={id => loaded = { ...loaded, [id]: true }} onerror={id => { failed = { ...failed, [id]: true }; if (selected[id]) skip([id]) }} /><span class="choice-mark">{selected[item.id] ? '✓' : choices[item.id]?.verdict === 'wrong' ? '×' : ''}</span></button>
          <div class="quiz-production"><ProductionBadge {item} />{#if item.state === 'flagged'}<span class="flagged-before" title="Someone flagged this crop earlier. Leave it unmarked to keep that flag, or mark what is wrong.">Flagged earlier</span>{/if}</div><div class="quiz-tile-tools">{#if keys[i]}<kbd>{keys[i]}</kbd>{/if}<span class="choice-label">{failed[item.id] ? 'Unavailable' : skipped[item.id] ? 'Skipped' : ''}</span><button class="inspect-choice" aria-label={`Inspect character ${i + 1}`} disabled={saving} onclick={() => inspectChoice(item)}>↗</button>{#if skipped[item.id]}<button class="restore-choice" aria-label={`Restore character ${i + 1}`} disabled={saving} onclick={() => restore(item.id)}>restore</button>{:else}<button class="skip-choice" aria-label={`Skip character ${i + 1}`} disabled={saving} onclick={() => skip([item.id])}>–</button>{/if}</div>
        </div>
      {/each}{/if}
    </div>
    {/key}
    {#if items.length}<div class="load-more-row" use:watchEnd><button class="load-more" disabled={loading || saving || loadingMore || !hasMore || items.length >= roundLimit} onclick={loadMore}>{loadingMore ? 'Loading…' : items.length >= roundLimit ? 'Save this round to load more' : hasMore ? `Load more ${reading}` : `All ${reading} loaded`}</button></div>{/if}
  {:else if current}
    <QuizFocus items={queue} index={focusIndex} label={step === 'issue' ? 'Choose the problem' : 'Correction'} backLabel={step === 'issue' ? 'Change selection' : 'Change problem'} disabled={saving} onback={back} onjump={jump} onprev={() => move(-1)} onnext={() => move(1)}>
      {#if step === 'issue'}
        <IssuePicker value={choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue ?? null} choose={assignCurrent} disabled={saving} />
      {:else}
        <p class="current-problem">{issueTitle(choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue)}</p>
        <ReadingSuggestions targetId={current.id} bind:element={suggestionsElement} result={suggestions[current.id]} loading={!suggestions[current.id]} contextResult={contextSuggestions[current.id] ?? null} contextLoading={!contextSuggestions[current.id]} issue={choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue} reading={current.label} value={choices[current.id]?.character || choices[current.id]?.correction} noneSelected={choices[current.id]?.noneSelected ?? false} disabled={saving} choose={(value, none) => chooseSuggestion(current.id, value, none)} />
      {/if}
    </QuizFocus>
  {/if}

  {#if step === 'select' && !loading && !items.length}<div class="empty"><span class="empty-mark">字</span><h2>{categories.length ? 'Choose a character to review.' : 'All caught up.'}</h2>{#if categories.length}<button class="primary" onclick={() => categoryOpen = true}>Choose character</button>{:else}<a href="#/flagged" class="primary">Review flagged characters →</a>{/if}</div>
  {:else}<div class="quiz-actionbar"><div class="round-selection"><span class="selection-dot" class:has-flags={decided > 0}></span><strong>{decided} issues</strong>{#if undecided}<span>{undecided} to decide</span>{/if}{#if Object.keys(skipped).length}<small>{Object.keys(skipped).length} skipped · not saved</small>{/if}{#if Object.keys(failed).length}<small>{Object.keys(failed).length} unavailable</small>{/if}</div><div class="quiz-submit">
    <span class="keyboard-hint">{step === 'select' ? 'qwerty… · Ctrl/⌘+Enter continues' : step === 'issue' ? '1–5 problem · ←→ crop' : 'n no correction · ←→ crop'}</span>
    <button class="quiet-link skip-selected" disabled={loading || saving || exhausted || (!decidable.length && !selection.length)} onclick={() => skip(selection.length ? selection : decidable.map(i => i.id))}>{selection.length ? `${SKIP_LABEL} selected` : SKIP_LABEL}</button>
    {#if exhausted || !selection.length}<button class="primary next-round" disabled={loading || saving || !canNext} onclick={() => exhausted ? load() : pass()}>Next character <span>→</span></button>
    {:else if step === 'select'}<button class="primary review-selected" disabled={loading || saving || loadingMore || !ready} onclick={reviewSelected}>Review selected ({selection.length}) <span>→</span></button>
    {:else if focusIndex < queue.length - 1}<button class="primary next-crop" disabled={loading || saving || !choices[current?.id]} onclick={primary}>Next crop <span>→</span></button>
    {:else if !answered}<button class="primary finish-issues" disabled={loading || saving || !choices[current?.id]} onclick={primary}>Review remaining <span>→</span></button>
    {:else}<button class="primary save-round" disabled={loading || saving || loadingMore || !ready} onclick={submit}>{saving ? 'Saving…' : 'Save issues'} <span>✓</span></button>{/if}
  </div></div>{/if}
</section>

<style>
  .review-material{display:flex;align-items:center;gap:10px;margin:0 0 20px;font-size:12px;color:var(--muted)}
  .review-material select{max-width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}
  /* Scoped to this view on purpose: the skipped state is the round's own, and the tile keeps the
     size and position it had so declining a crop does not reflow the grid under the reader. */
  .quiz-production{padding:0 12px 4px;display:flex;gap:8px;align-items:baseline}
  .flagged-before{font-size:10px;color:var(--wrong)}
  .quiz-tile.skipped { background: #eceaf0; border-style: dashed; }
  .quiz-tile.skipped .quiz-choice { opacity: .35; }
  .restore-choice { font-size: 9px; padding: 1px 7px; }
  /* One stage at a time: the selection toolbar belongs to step one and is not shown again. */
  .stage-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0 10px; font-size: 12px; }
  .stage-toolbar strong { font-weight: 500; }
  .character-history { display:flex; align-items:center; gap:10px; margin:0 0 20px; min-width:0; }
  .character-history > button { flex-shrink:0; font-size:11px; }
  .history-characters { display:flex; gap:6px; overflow-x:auto; min-width:0; padding:3px; }
  .history-character { min-width:38px; padding:5px 9px; font-size:20px; }
  .history-character.current { color:var(--accent); background:var(--accent-light); border-color:var(--accent); }
  .load-more-row { display:flex; justify-content:center; padding:22px 0 0; }
  .load-more { min-width:170px; font-size:13px; }
  .current-problem { color: var(--muted); font-size: 12px; margin: 0 0 14px; }
</style>
