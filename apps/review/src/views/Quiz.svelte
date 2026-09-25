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
  import { issues, issueTitle, suggestsReading, isSingle, greetSuggestions, skipLabel, skipHint } from '../lib/issues.js'
  import { nextCharacter, ROUND_BATCH, MORE_BATCH, REFERENCE_LIMIT, mergeReferences } from '../lib/reviewRounds.js'
  import { t, around } from '../lib/i18n.svelte.js'
  let { clientId, initialReading = '', inspect } = $props()
  let data = $state(null), items = $state([]), choices = $state({}), selected = $state({})
  let loaded = $state({}), failed = $state({}), suggestions = $state({}), contextSuggestions = $state({})
  // Crops the reader declined to judge: no choice, no request, not counted (see `skip`).
  let skipped = $state({})
  // What this round already saved, by crop: 'seen', 'skip' or 'flagged'. A saved crop stays in its
  // round, so going back through the history shows it, but it is never sent a second time.
  let recorded = $state({})
  // Crops at least half of which have been on screen this round. Only these can be recorded as seen:
  // a batch that loaded below the fold was never looked at, and passing it would drop it for good.
  let viewed = $state({})
  let reading = $state(''), loading = $state(true), saving = $state(false), error = $state(''), errorStatus = $state(0)
  let categoryOpen = $state(false), search = $state(''), completed = $state(0), last = $state(null)
  let roundId = $state(crypto.randomUUID()), requestId = 0, closed = false
  let roundSeed = randomSeed()
  // Already-confirmed and already-seen crops of the current reading, for comparison against the
  // round on screen. Loaded after the round itself, and a failure here never blocks the round.
  let references = $state([])
  let history = $state([]), historyIndex = $state(-1)
  let loadingMore = $state(false), hasMore = $state(false), nextOffset = $state(0), loadMoreFailed = $state(false)
  // A saved round says so, since the next character replaces it at once.
  let savedNotice = $state(''), savedTimer
  function announceSaved(count, label) {
    clearTimeout(savedTimer)
    savedNotice = t('quiz.roundSaved', { count, reading: label })
    savedTimer = setTimeout(() => savedNotice = '', 3500)
  }
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
      // About two screens ahead, so the next crops are usually in before the reader gets there.
      { rootMargin: '0px 0px 1600px 0px' })
    observer.observe(node)
    return { destroy() { observer.disconnect(); nearEnd = false } }
  }
  $effect(() => {
    // Never while an error is showing: a failed batch would otherwise be retried at once, forever,
    // and an error from another action would be cleared before anyone could read it.
    if (nearEnd && hasMore && !error && !loading && !saving && !loadingMore && items.length < roundLimit) loadMore()
  })
  const roundLimit = $derived(data?.review_limit ?? 4096)
  // The scopes a round can be dealt from, in the order the material menu lists them.
  const MATERIALS = [
    ['not:printed/type', () => t('quiz.material.excludeMovableType')],
    ['handwritten', () => t('production.kind.handwritten')],
    ['inscribed', () => t('production.kind.inscribed')],
    ['printed', () => t('production.kind.printed')],
    ['printed/woodblock', () => t('production.kind.printed_woodblock')],
    ['printed/type', () => t('production.kind.printed_type')],
    ['typewritten', () => t('production.kind.typewritten')],
    ['mixed', () => t('production.kind.mixed')],
    ['unknown', () => t('production.kind.unknown')],
    ['all', () => t('quiz.material.all')],
  ]
  let production = $state('not:printed/type')
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
  const remaining = $derived(items.filter(i => loaded[i.id] && !failed[i.id] && !skipped[i.id] && recorded[i.id] !== 'flagged'))
  const settled = $derived(items.every(i => skipped[i.id] || failed[i.id] || (loaded[i.id] && !failed[i.id])))
  const ready = $derived(items.length > 0 && settled && remaining.length > 0)
  const exhausted = $derived(items.length > 0 && settled && !remaining.length)
  const categories = $derived((data?.categories ?? []).filter(c => c.pending > 0 && c.label.includes(search)))

  // Whether moving on would record something: a crop seen, or a crop skipped after it was seen.
  const recordable = $derived(!selection.length && (remaining.some(i => viewed[i.id] && !recorded[i.id])
    || items.some(i => skipped[i.id] && viewed[i.id] && !failed[i.id] && !recorded[i.id])))
  // A round or a crop changed under the reader: the error offers to reload the round in place.
  const stale = $derived(Boolean(error) && errorStatus === 409)
  const canNext = $derived((data?.categories ?? []).some(c => c.pending > 0 && c.label !== reading))
  function snapshot() {
    return $state.snapshot({ reading, items, choices, selected, skipped, recorded, suggestions, contextSuggestions,
      roundId, roundSeed, hasMore, nextOffset, production, summary: data })
  }
  function checkpoint() {
    if (historyIndex >= 0) history[historyIndex] = snapshot()
  }
  function restoreRound(round) {
    production = round.production; data = round.summary
    reading = round.reading; items = round.items; choices = round.choices; selected = round.selected
    skipped = round.skipped; recorded = round.recorded ?? {}; suggestions = round.suggestions; contextSuggestions = round.contextSuggestions
    roundId = round.roundId; roundSeed = round.roundSeed; hasMore = round.hasMore; nextOffset = round.nextOffset ?? 0
    loaded = {}; failed = {}; viewed = {}; step = 'select'; at = 0; error = ''; errorStatus = 0; categoryOpen = false
  }
  function visit(index) {
    if (saving || loading || index < 0 || index >= history.length || index === historyIndex) return
    checkpoint()
    ++requestId; loadingMore = false
    historyIndex = index
    restoreRound($state.snapshot(history[index]))
  }
  async function chooseCategory(target) {
    categoryOpen = false
    if (target === reading || saving) return
    if (!(await record())) return
    const previous = history.findLastIndex(round => round.reading === target && round.production === production)
    if (previous >= 0) visit(previous)
    else load({ target })
  }
  async function load({ target = null, replace = false, scope = production } = {}) {
    checkpoint()
    const id = ++requestId
    loading = true; loadingMore = false; error = ''; errorStatus = 0; categoryOpen = false
    try {
      const summary = await catalogue({ purpose: 'review', reviewer: clientId, production: scope, limit: 1, state: 'pending' })
      if (closed || id !== requestId) return
      data = summary
      const epochKey = 'atlas.review-epoch.' + clientId
      if (stored(epochKey, null) !== (summary.review_epoch ?? null)) {
        last = null; completed = 0; history = []; historyIndex = -1
        remember('atlas.last-round.' + clientId, null)
        remember(epochKey, summary.review_epoch ?? null)
      }
      // A character whose count promised crops that cannot be dealt gives way to the next one.
      let available = summary.categories.filter(c => c.pending > 0), chosen = null, seed = randomSeed(), result = null
      for (let tries = 0; tries < 8; tries++) {
        chosen = tries === 0 && target && available.some(c => c.label === target) ? target
          : nextCharacter(available, scope === production ? reading : '', history.filter(r => r.production === scope), randomSeed())
        if (!chosen) break
        result = await catalogue({ purpose: 'review', reviewer: clientId, production: scope, reading: chosen, state: 'pending', limit: ROUND_BATCH, seed })
        if (closed || id !== requestId) return
        if (result.items.length) break
        available = available.filter(c => c.label !== chosen)
        chosen = null
      }
      if (!chosen) {
        if (scope !== production) {
          restoreRound({ reading: '', items: [], choices: {}, selected: {}, skipped: {}, suggestions: {},
            contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: randomSeed(), hasMore: false,
            production: scope, summary })
          historyIndex = -1
        } else if (!reading) items = []
        else { error = t('quiz.noOtherCharacters'); errorStatus = 0 }
        return
      }
      restoreRound({ reading: chosen, items: arranged(numbered(unique(result.items))), choices: {}, selected: {}, skipped: {},
        suggestions: {}, contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: seed,
        hasMore: (result.next_offset ?? result.items.length) < result.total, nextOffset: result.next_offset ?? result.items.length,
        production: scope, summary })
      if (replace && historyIndex >= 0) history[historyIndex] = snapshot()
      else {
        history = [...history.slice(0, historyIndex + 1), snapshot(), ...history.slice(historyIndex + 1)]
        historyIndex += 1
      }
    } catch (e) { if (!closed && id === requestId) { error = e.message; errorStatus = e.status ?? 0 } }
    finally { if (!closed && id === requestId) loading = false }
  }
  async function loadMore() {
    if (loading || saving || loadingMore || !hasMore || items.length >= roundLimit) return
    const id = requestId, round = roundId
    loadingMore = true; loadMoreFailed = false; error = ''; errorStatus = 0
    const seen = new Set(items.map(item => item.id))
    // Continue from where the last load stopped, a batch early: reviews saved meanwhile may have moved
    // rows forward. Crops already on screen are dropped by occurrence, so the overlap costs nothing.
    let offset = Math.max(0, nextOffset - MORE_BATCH), additions = [], more = false
    try {
      const batchLimit = Math.min(MORE_BATCH, roundLimit - items.length)
      while (additions.length < batchLimit) {
        const result = await catalogue({ purpose: 'review', reviewer: clientId, production, reading, state: 'pending', limit: 48, offset, seed: roundSeed })
        if (closed || id !== requestId || round !== roundId) return
        const fresh = unique(result.items).filter(item => !seen.has(item.id))
        const room = batchLimit - additions.length
        const batch = fresh.slice(0, room)
        additions.push(...batch)
        batch.forEach(item => seen.add(item.id))
        // The service may pass over crops it will not deal, so its next offset can run ahead of the items.
        const next = result.next_offset ?? offset + result.items.length, moved = next > offset
        offset = next
        if (additions.length >= batchLimit) {
          more = fresh.length > room || offset < result.total
          break
        }
        if (!moved || offset >= result.total) { more = false; break }
      }
      // Added crops follow the ones already on screen, so nothing the reviewer is looking at moves.
      items = [...items, ...arranged(numbered(additions, items.length))]; hasMore = more; nextOffset = offset
    } catch (e) { if (id === requestId) { error = e.message; errorStatus = e.status ?? 0; loadMoreFailed = true } }
    finally { if (id === requestId) loadingMore = false }
  }
  /**
   * The reference strip for the current reading: crops already confirmed, then crops already
   * seen. Reloaded whenever the round changes; a failure here just leaves the strip empty, since
   * it is a comparison aid, not a decision the round depends on.
   */
  async function loadReferences() {
    const target = reading, scope = production, round = roundId
    if (!target) { references = []; return }
    try {
      const [checked, seen] = await Promise.all([
        catalogue({ purpose: 'review', reviewer: clientId, production: scope, reading: target, state: 'checked', limit: REFERENCE_LIMIT, seed: roundSeed }),
        catalogue({ purpose: 'review', reviewer: clientId, production: scope, reading: target, state: 'seen', limit: REFERENCE_LIMIT, seed: roundSeed }),
      ])
      if (closed || round !== roundId) return
      references = mergeReferences(checked.items ?? [], seen.items ?? [])
    } catch { if (!closed && round === roundId) references = [] }
  }
  $effect(() => { void roundId; loadReferences() })
  const decidable = $derived(remaining)
  const available = $derived(decidable.length)

  // Step one: choose the crops that are wrong. A selection is not a verdict, so nothing is assigned
  // here, and a crop taken out of the selection gives up the answer it had.
  function toggle(id) {
    if (saving || loading || !loaded[id] || failed[id] || recorded[id] === 'flagged') return
    if (skipped[id]) { skipped = without(skipped, [id]); selected = { ...selected, [id]: true }; at = 0; return }
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
    const previous = choices[id]?.issue ?? null
    const kept = compatible(choices[id], issue, previous)
    choices = { ...choices, [id]: { verdict: 'wrong', issue, ...kept } }
    if (skipped[id]) skipped = without(skipped, [id])
    if (suggestsReading(issue)) suggest(items.find(i => i.id === id))
    error = ''; errorStatus = 0
  }
  async function assignCurrent(issue) {
    if (!current) return
    const id = current.id
    assign(current.id, issue)
    step = suggestsReading(issue) ? 'correct' : 'issue'
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
   * A skip is not a verdict and is not counted: the crop stays pending. When the round is saved or
   * passed, the skip is recorded against the reviewer, so other reviewers are dealt it first.
   */
  function skip(ids = selection) {
    if (saving || loading) return
    const wanted = ids.filter(id => items.some(i => i.id === id))
    if (!wanted.length) return
    const skippingCurrent = step !== 'select' && current && wanted.includes(current.id)
    skipped = { ...skipped, ...Object.fromEntries(wanted.map(id => [id, true])) }
    choices = without(choices, wanted)
    // On the selection screen a skip also leaves the selection; in the one-crop steps the crop keeps
    // its place, so the reader can go back to it and choose a problem after all.
    if (step === 'select') selected = without(selected, wanted)
    error = ''; errorStatus = 0
    if (skippingCurrent && focusIndex < queue.length - 1) move(1)
    else if (skippingCurrent) step = 'issue'
  }
  // A crop names the pixels it was shown with: a local crop by its page hash, a corpus glyph by its
  // source revision, which covers its box and image.
  const pixels = i => i.origin === 'corpus' ? { source_revision: i.source_revision } : { image_sha256: i.image_sha256 }
  /** Take a crop out of the round: a review saved elsewhere has decided it. */
  function drop(id) {
    items = items.filter(i => i.id !== id)
    choices = without(choices, [id]); selected = without(selected, [id]); skipped = without(skipped, [id])
  }
  function inspectChoice(item) {
    // A corpus glyph opens in the corpus reviewer, which saves its own review; the round then drops it.
    if (item.origin === 'corpus') { inspect(item.id, null, [], drop, 'corpus'); return }
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
    error = ''; errorStatus = 0
  }
  function jump(i) {
    at = Math.max(0, Math.min(queue.length - 1, i))
    const item = queue[at], choice = choices[item?.id]
    step = suggestsReading(choice?.issue) ? 'correct' : 'issue'
    if (item && step === 'correct' && (!suggestions[item.id] || !contextSuggestions[item.id])) suggest(item)
    error = ''; errorStatus = 0
  }
  const move = delta => jump(focusIndex + delta)

  /** The action the primary button offers, and the one Ctrl/Cmd+Enter performs. */
  function primary() {
    if (saving || loading || loadingMore) return
    if (!selection.length) { pass(); return }
    if (step === 'select') { reviewSelected(); return }
    if (!current || !(choices[current.id] || skipped[current.id])) return
    if (focusIndex < queue.length - 1) { move(1); return }
    if (!answered) { jump(selectedItems.findIndex(i => !choices[i.id] && !skipped[i.id])); return }
    submit()
  }
  /** The crops this round skipped. A skip is recorded against the reviewer, not as a decision: other
   * reviewers are dealt the crop first, and it comes back to this one only after a rest. */
  function skippedCrops() {
    return items.filter(i => skipped[i.id] && !failed[i.id] && viewed[i.id] && !recorded[i.id]).map(i => ({ id: i.id, ...pixels(i), image: i.image }))
  }
  function markRecorded(crops, how) { recorded = { ...recorded, ...Object.fromEntries(crops.map(crop => [crop.id, how])) } }
  async function submit() {
    if (saving || loadingMore || !ready) return
    if (step === 'select') return
    if (selection.length && !answered) { error = t('quiz.giveEachProblem'); errorStatus = 0; return }
    // Build an atomic request from explicit problems only; UI choice state never enters the API.
    const marked = new Set(selection)
    const answers = remaining.filter(i => marked.has(i.id) && choices[i.id]?.verdict === 'wrong').map(i => {
      const { character, noneSelected, ...rest } = choices[i.id]
      return { id: i.id, revision: i.revision, ...pixels(i), ...rest,
               ...(character ? { character } : {}) }
    })
    // Every other crop the round showed was seen and left unflagged. That is not a confirmation,
    // but it is recorded, so the crop is not dealt again.
    const flagged = new Set(answers.map(answer => answer.id))
    const seen = remaining.filter(i => !flagged.has(i.id) && viewed[i.id] && !recorded[i.id]).map(i => ({ id: i.id, ...pixels(i), image: i.image }))
    const passed = skippedCrops()
    if (!answers.length && !seen.length && !passed.length) { await load(); return }
    saving = true; error = ''; errorStatus = 0
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, answers, seen, skipped: passed })
      last = { id: roundId, count: answers.length, label: reading, production }
      remember('atlas.last-round.' + clientId, last); completed += answers.length
      announceSaved(answers.length + seen.length + passed.length, reading)
      markRecorded(answers, 'flagged'); markRecorded(seen, 'seen'); markRecorded(passed, 'skip')
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      await load()
    } catch (e) { error = e.status === 409 ? t('quiz.roundChanged') : e.message; errorStatus = e.status ?? 0 }
    finally { saving = false }
  }
  // Moving on from a round with nothing flagged: the crops it showed were seen, so they are recorded
  // as seen before the next round is dealt. Skipped crops are recorded as skipped; crops that failed
  // to load were not seen at all.
  /** Record what a round with nothing selected showed: the crops seen, and the crops skipped. Every
   * way out of such a round goes through here, so none of them drops the round's record. */
  async function record() {
    const seen = remaining.filter(i => viewed[i.id] && !recorded[i.id]).map(i => ({ id: i.id, ...pixels(i), image: i.image }))
    const passed = skippedCrops()
    if ((!seen.length && !passed.length) || selection.length) return true
    saving = true; error = ''; errorStatus = 0
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, seen, skipped: passed })
      last = { id: roundId, count: 0, label: reading, production }
      remember('atlas.last-round.' + clientId, last)
      announceSaved(seen.length + passed.length, reading)
      markRecorded(seen, 'seen'); markRecorded(passed, 'skip')
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      return true
    } catch (e) { error = e.message; errorStatus = e.status ?? 0; return false }
    finally { saving = false }
  }
  async function pass() {
    if (saving || loading || loadingMore) return
    if (await record()) await load()
  }
  async function undo() {
    if (!last || saving) return
    saving = true; error = ''; errorStatus = 0
    try {
      await request(`/atlas/rounds/${last.id}/undo`, { client_id: clientId })
      // A round stored by an older version may name a scope the menu no longer offers.
      const target = last.label, scope = MATERIALS.some(([value]) => value === last.production) ? last.production : production
      completed = Math.max(0, completed - last.count); last = null
      remember('atlas.last-round.' + clientId, null); await load({ target, scope })
    } catch (e) { error = e.message; errorStatus = e.status ?? 0 }
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
    if (step === 'issue' && /^[1-4]$/.test(e.key)) { e.preventDefault(); assignCurrent(issues[Number(e.key) - 1].id) }
    if (step === 'issue' && (e.key.toLowerCase() === 's' || e.key === '5') && current) { e.preventDefault(); skip([current.id]) }
    if (step === 'correct' && e.key.toLowerCase() === 'n' && !control) { e.preventDefault(); if (current) chooseSuggestion(current.id, null, true) }
  }
  // A round is dealt as before; shown in shape order, the crops of one form sit together and a crop
  // unlike its neighbours stands out. `dealt` keeps the dealt order for switching back.
  let byShape = $state(stored('atlas.quiz.shape-order', true) !== false)
  // One tile per crop: the grid is keyed by id, and a repeated id would stop the round rendering.
  function unique(list) { const ids = new Set(); return list.filter(item => !ids.has(item.id) && ids.add(item.id)) }
  function numbered(list, from = 0) { return list.map((item, i) => ({ ...item, dealt: item.dealt ?? from + i })) }
  function arranged(list) {
    return [...list].sort((a, b) => byShape
      ? (a.shape_order ?? Infinity) - (b.shape_order ?? Infinity) || a.dealt - b.dealt
      : a.dealt - b.dealt)
  }
  function toggleShape() { byShape = !byShape; remember('atlas.quiz.shape-order', byShape); items = arranged(items) }
  onMount(() => { last = stored('atlas.last-round.' + clientId, null); load({ target: initialReading || null }); return () => { closed = true } })
</script>

<svelte:window onkeydown={keydown} />
<section class="quiz-workspace">
  <div class="quiz-topline"><a href="#/" class="quiet-link">{t('quiz.backToCollection')}</a><div class="round-count"><span class="live-dot"></span>{t('quiz.issuesSavedSession', { count: completed })}</div><button class="undo-round shape-toggle" aria-pressed={byShape} onclick={toggleShape}>{byShape ? t('quiz.shapeToggle.byShape') : t('quiz.shapeToggle.dealt')}</button>{#if last}<button class="undo-round" disabled={saving} onclick={undo}>{t('quiz.undoLastRound')}</button>{/if}</div>
  <div class="quiz-heading"><div class="target-character" aria-label={t('quiz.targetReading', { reading })}><span lang="ja">{reading || '字'}</span></div><div class="quiz-title"><p class="overline">{t('quiz.overline', { step: step === 'select' ? t('quiz.overline.select') : t('quiz.overline.review') })}</p><h1>{step === 'select' ? t('quiz.heading.select') : step === 'issue' ? t('quiz.heading.issue') : choices[current?.id]?.issue === 'merged' ? t('quiz.heading.merged') : t('quiz.heading.correct')}</h1>{#if step === 'select'}<p>{around('quiz.selectHint', 'reading')[0]}<b>{reading || "…"}</b>{around('quiz.selectHint', 'reading')[1]}</p>{/if}</div><div class="round-switch"><button class="category-toggle" disabled={saving || loading} onclick={() => categoryOpen = !categoryOpen}>{t('quiz.changeCharacter')}</button><button class="quiet-link" disabled={saving || loading || (!canNext && !recordable)} onclick={pass}>{t('quiz.nextCharacterArrow')}</button></div></div>
  {#if categoryOpen}<div class="round-categories"><input aria-label={t('quiz.findCategory.aria')} placeholder={t('quiz.findCategory.placeholder')} bind:value={search}/><div class="category-options">{#each categories as c}<button disabled={saving} onclick={() => chooseCategory(c.label)}><span lang="ja">{c.label}</span><small>{c.pending}</small></button>{/each}</div></div>{/if}
  <label class="review-material">{t('quiz.material.label')}
    <select aria-label={t('quiz.material.aria')} value={production} disabled={saving || loading || loadingMore}
      onchange={event => load({ scope: event.currentTarget.value, target: reading })}>
      {#each MATERIALS as [value, label]}<option {value}>{label()}</option>{/each}
    </select>
  </label>
  {#if history.length > 1}
    <nav class="character-history" aria-label={t('quiz.history.label')}>
      <button class="previous-reading" aria-label={t('quiz.history.previousRound')} disabled={saving || loading || historyIndex <= 0} onclick={() => visit(historyIndex - 1)}>{t('quiz.history.previous')}</button>
      <div class="history-characters">{#each history as round, i (round.roundId)}<button class="history-character" class:current={i === historyIndex} aria-current={i === historyIndex ? 'step' : undefined} aria-label={t('quiz.history.returnTo', { reading: round.reading })} disabled={saving || loading} onclick={() => visit(i)}>{round.reading}</button>{/each}</div>
      <button class="forward-reading" aria-label={t('quiz.history.nextRound')} disabled={saving || loading || historyIndex >= history.length - 1} onclick={() => visit(historyIndex + 1)}>→</button>
    </nav>
  {/if}
  {#if error}<div class="error-message" role="alert"><span>{error}</span>{#if stale}<button disabled={saving} onclick={() => load({ target: reading, replace: true })}>{t('quiz.reloadRound')}</button>{/if}</div>{/if}

  {#if step === 'select'}
    <div class="stage-toolbar"><strong>{selection.length ? t('quiz.select.selectedCount', { count: selection.length }) : t('quiz.select.selectProblems')}</strong><button class="bulk-toggle" disabled={saving || loading || !available} onclick={selectAll}>{selection.length === decidable.length && selection.length ? t('quiz.bulk.none') : t('quiz.bulk.all')}</button><button class="bulk-toggle skip-selection" disabled={saving || loading || !selection.length} onclick={() => skip()} title={skipHint()}>{t('quiz.skipSelected', { skip: skipLabel() })}</button><span class="keyboard-hint">{t('quiz.keyboardHint.pickCrop')}</span></div>
    {#key roundId}<div class="quiz-grid" aria-label={t('quiz.grid.label')} aria-busy={loading}>
      {#if loading}{#each Array(12) as _}<div class="quiz-skeleton"></div>{/each}
      {:else}{#each items as item, i (item.id)}
        <div class="quiz-tile" use:watchSeen={item.id} data-unit={item.id} class:selected={selected[item.id]} class:wrong={choices[item.id]?.verdict === 'wrong' || recorded[item.id] === 'flagged'} class:unavailable={failed[item.id]} class:skipped={skipped[item.id]} class:recorded={recorded[item.id]}>
          <button class="quiz-choice" aria-label={t('quiz.selectCharacter', { number: i + 1 })} aria-pressed={!!selected[item.id]} disabled={saving || !loaded[item.id] || recorded[item.id] === 'flagged'} onclick={() => toggle(item.id)}><Glyph {item} eager onload={id => loaded = { ...loaded, [id]: true }} onerror={id => { failed = { ...failed, [id]: true }; if (selected[id]) skip([id]) }} /><span class="choice-mark">{selected[item.id] ? '✓' : choices[item.id]?.verdict === 'wrong' ? '×' : ''}</span></button>
          <div class="quiz-production"><ProductionBadge {item} />{#if recorded[item.id]}<span class="recorded-badge">✓ {t('app.saved')}</span>{/if}{#if item.origin === 'corpus'}<span class="quiz-source" lang={item.source?.title ? 'ja' : undefined} title={item.source?.title}>{item.source?.title ?? t('quiz.corpusSource')}</span>{/if}</div><div class="quiz-tile-tools">{#if keys[i]}<kbd>{keys[i]}</kbd>{/if}<span class="choice-label">{failed[item.id] ? t('quiz.choiceLabel.unavailable') : skipped[item.id] ? t('quiz.choiceLabel.skipped') : ''}</span><button class="inspect-choice" aria-label={t('quiz.inspectCharacter', { number: i + 1 })} disabled={saving} onclick={() => inspectChoice(item)}>↗</button>{#if skipped[item.id]}<button class="restore-choice" aria-label={t('quiz.restoreCharacter', { number: i + 1 })} disabled={saving} onclick={() => restore(item.id)}>{t('quiz.restore')}</button>{:else}<button class="skip-choice" aria-label={t('quiz.skipCharacter', { number: i + 1 })} title={skipHint()} disabled={saving} onclick={() => skip([item.id])}>–</button>{/if}</div>
        </div>
      {/each}{#if loadingMore}{#each Array(6) as _}<div class="quiz-skeleton" aria-hidden="true"></div>{/each}{/if}{/if}
    </div>
    {/key}
    <!-- More crops load on their own as the reader nears the end; the row only says what is happening.
         A button remains for the case scrolling cannot trigger (a failed batch waits for a retry). -->
    {#if items.length}<div class="load-more-row" use:watchEnd role="status">
      {#if loadingMore}<span class="load-more-status"><span class="load-more-spinner" aria-hidden="true"></span>{t('quiz.loadMore.loading')}</span>
      {:else if hasMore && items.length >= roundLimit}<span class="load-more-status">{t('quiz.loadMore.saveToLoad')}</span>
      {:else if hasMore}<button class="load-more" disabled={loading || saving} onclick={loadMore}>{loadMoreFailed && error ? t('common.retry') : t('quiz.loadMore.more', { reading })}</button>
      {:else}<span class="load-more-status">{t('quiz.loadMore.allLoaded', { reading })}</span>{/if}
    </div>{/if}
    {#if references.length}
      <section class="quiz-reference" aria-label={t('quiz.reference.label')}>
        <p class="reference-heading">{t('quiz.reference.heading')} <span>{references.length}</span></p>
        <div class="reference-strip">
          {#each references as item (item.id)}
            <button class="reference-tile" aria-label={t('quiz.reference.inspect', { label: item.label, state: item.referenceState === 'checked' ? t('quiz.reference.confirmed') : t('quiz.reference.seen') })} onclick={() => inspect(item.id)}>
              <span class="reference-glyph"><Glyph {item} /></span>
              <span class="reference-tag"><span class="status-dot" class:checked={item.referenceState === 'checked'} class:seen={item.referenceState === 'seen'}></span>{item.referenceState === 'checked' ? t('quiz.reference.confirmed') : t('quiz.reference.seen')}</span>
            </button>
          {/each}
        </div>
      </section>
    {/if}
  {:else if current}
    <QuizFocus items={queue} {skipped} index={focusIndex} label={step === 'issue' ? t('quiz.focus.chooseProblem') : t('quiz.focus.correction')} backLabel={step === 'issue' ? t('quiz.focus.changeSelection') : t('quiz.focus.changeProblem')} disabled={saving} onback={back} onjump={jump} onprev={() => move(-1)} onnext={() => move(1)}>
      {#if step === 'issue'}
        <IssuePicker value={choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue ?? null} choose={assignCurrent} disabled={saving}
          skip={() => skip([current.id])} skipped={!!skipped[current.id]} />
      {:else}
        <p class="current-problem">{issueTitle(choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue)}</p>
        <ReadingSuggestions targetId={current.id} bind:element={suggestionsElement} result={suggestions[current.id]} loading={!suggestions[current.id]} contextResult={contextSuggestions[current.id] ?? null} contextLoading={!contextSuggestions[current.id]} issue={choices[current.id]?.issue === 'character' ? 'reading' : choices[current.id]?.issue} reading={current.label} value={choices[current.id]?.character || choices[current.id]?.correction} noneSelected={choices[current.id]?.noneSelected ?? false} disabled={saving} choose={(value, none) => chooseSuggestion(current.id, value, none)} />
      {/if}
    </QuizFocus>
  {/if}

  {#if step === 'select' && !loading && !items.length}<div class="empty"><span class="empty-mark">字</span><h2>{categories.length ? t('quiz.empty.chooseCharacter') : t('quiz.empty.allCaughtUp')}</h2>{#if categories.length}<button class="primary" onclick={() => categoryOpen = true}>{t('quiz.chooseCharacterButton')}</button>{:else}<a href="#/flagged" class="primary">{t('quiz.reviewFlagged')}</a>{/if}</div>
  {:else}<div class="quiz-actionbar"><div class="round-selection"><span class="selection-dot" class:has-flags={decided > 0}></span><strong>{t('quiz.decided', { count: decided })}</strong>{#if undecided}<span>{t('quiz.undecided', { count: undecided })}</span>{/if}{#if Object.keys(skipped).length}<small>{t('quiz.skippedNotSaved', { count: Object.keys(skipped).length })}</small>{/if}{#if Object.keys(failed).length}<small>{t('quiz.unavailableCount', { count: Object.keys(failed).length })}</small>{/if}</div><div class="quiz-submit">
    <span class="keyboard-hint">{step === 'select' ? t('quiz.keyboardHint.select') : step === 'issue' ? t('quiz.keyboardHint.issue') : t('quiz.keyboardHint.correct')}</span>
    {#if step === 'select'}<button class="quiet-link skip-selected" disabled={loading || saving || exhausted || (!decidable.length && !selection.length)} onclick={() => skip(selection.length ? selection : decidable.map(i => i.id))} title={skipHint()}>{selection.length ? t('quiz.skipSelected', { skip: skipLabel() }) : skipLabel()}</button>{/if}
    {#if exhausted || !selection.length}<button class="primary next-round" disabled={loading || saving || (!canNext && !recordable)} onclick={pass}>{t('quiz.nextCharacterLabel')} <span>→</span></button>
    {:else if step === 'select'}<button class="primary review-selected" disabled={loading || saving || loadingMore || !ready} onclick={reviewSelected}>{t('quiz.reviewSelected', { count: selection.length })} <span>→</span></button>
    {:else if focusIndex < queue.length - 1}<button class="primary next-crop" disabled={loading || saving || !(choices[current?.id] || skipped[current?.id])} onclick={primary}>{t('quiz.nextCrop')} <span>→</span></button>
    {:else if !answered}<button class="primary finish-issues" disabled={loading || saving || !(choices[current?.id] || skipped[current?.id])} onclick={primary}>{t('quiz.reviewRemaining')} <span>→</span></button>
    {:else}<button class="primary save-round" disabled={loading || saving || loadingMore || !ready} onclick={submit}>{saving ? t('common.saving') : t('quiz.saveIssues')} <span>✓</span></button>{/if}
  </div></div>{/if}
</section>

{#if savedNotice}<div class="save-toast quiz-saved" role="status">✓ {savedNotice}</div>{/if}
<style>
  .review-material{display:flex;align-items:center;gap:10px;margin:0 0 20px;font-size:12px;color:var(--muted)}
  .review-material select{max-width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}
  /* Scoped to this view on purpose: the skipped state is the round's own, and the tile keeps the
     size and position it had so declining a crop does not reflow the grid under the reader. */
  .quiz-production{display:flex;gap:6px;min-width:0;padding:0 12px 4px}
  .quiz-source{flex:1;min-width:0;overflow:hidden;font-size:10px;line-height:1.5;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}
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
  .load-more-status { display:inline-flex; align-items:center; gap:8px; min-height:38px; font-size:13px; color:var(--muted); }
  .load-more-spinner { width:14px; height:14px; border:2px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:load-more-spin .8s linear infinite; }
  @keyframes load-more-spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .load-more-spinner { animation:none; } }
  /* Comparison aid, not part of the round: smaller tiles, no selection state, own inspect only. */
  .quiz-reference { margin:26px 0 4px; }
  .reference-heading { display:flex; align-items:center; gap:8px; font-size:11px; color:var(--muted); margin:0 0 10px; }
  .reference-heading span { font-variant-numeric:tabular-nums; }
  .reference-strip { display:flex; flex-wrap:wrap; gap:8px; }
  .reference-tile { display:flex; flex-direction:column; align-items:center; gap:4px; width:68px; padding:6px 4px; background:#f1f1f3; border:1px solid transparent; border-radius:7px; }
  .reference-tile:hover { border-color:#ceccd9; }
  .reference-glyph { display:block; width:52px; height:52px; }
  .reference-glyph :global(img) { width:100%; height:100%; object-fit:contain; }
  .reference-tag { display:flex; align-items:center; gap:4px; font-size:8px; color:var(--muted); white-space:nowrap; }
  .status-dot.seen { background:#8d8d95; }
  .current-problem { color: var(--muted); font-size: 12px; margin: 0 0 14px; }
  /* Above the sticky save bar, which would otherwise sit behind it. */
  .save-toast.quiz-saved { bottom:108px; }
  /* A crop this round already saved: still shown, marked, and never sent again. */
  .recorded-badge { font-size:9px; color:var(--muted); white-space:nowrap; }
  .quiz-tile.recorded .quiz-choice { opacity:.72; }
</style>
