<script>
  import ContributionTerms from '../components/ContributionTerms.svelte'
  import ProductionBadge from '../components/ProductionBadge.svelte'
  import ImageStyleToggle from '../components/ImageStyleToggle.svelte'
  import TileSizeSlider from '../components/TileSizeSlider.svelte'
  import QuizGuide from '../components/QuizGuide.svelte'
  // Select problems, then give each crop its issue, with the correction offered under it, before moving on.
  // Only explicitly marked problems are saved as reviews. An unmarked crop that was on screen is
  // recorded as seen, which keeps it out of later rounds without confirming it.
  import { onMount, tick, untrack } from 'svelte'
  import { undoOutcome } from '../lib/undoOutcome.js'
  import { pushState, replaceState } from '$app/navigation'
  import { page, updated } from '$app/state'
  import Glyph from '../components/Glyph.svelte'
  import CropReview from '../components/CropReview.svelte'
  import CropForm from '../components/CropForm.svelte'
  import { markForms, unmarkForms } from '../lib/cropForms.js'
  import { styleCrops, styleOf, unstyleCrops } from '../lib/cropStyle.js'
  import QuizFocus from '../components/QuizFocus.svelte'
  import CopyId from '../components/CopyId.svelte'
  import FormBar from '../components/FormBar.svelte'
  import FormText from '../components/FormText.svelte'
  import { graphemeLabel, labelParts, labelSize } from '$worker/graphemeLabel.ts'
  import ScriptText from '../components/ScriptText.svelte'
  import ScriptLine from '../components/ScriptLine.svelte'
  import { catalogue, character, cropPixels, randomSeed, request, remember, stored, number, suggestionsFor } from '../lib/client.js'
  import { cropDetails } from '../lib/cropDetails.js'
  import { keptCrops, keptState, keyOf } from '../lib/outbox.svelte.js'
  import {
    confirmedCharacter, confirmationPlan,
    correctionBatches, sendBatches, sendEach,
  } from '../lib/corrections.js'
  import { ROUND_MAX } from '../../../cloudflare/src/rounds.ts'
  import { arrowTarget } from '../lib/selection.js'
  import { isDescription } from '../lib/ids.js'
  import { offersSuggestions, isSingle, greetSuggestions, skipLabel, skipHint, toggleIssue, issueFields } from '../lib/issues.js'
  import {
    dealtRound, firstCrops, nextGrapheme, roundGraphemes, graphemeText, roundAddress,
    ROUND_BATCH, MORE_BATCH, REFERENCE_LIMIT, mergeReferences,
    QUIZ_TILE_SIZES, QUIZ_CARDS_SIZE, QUIZ_COMPACT_SIZE, QUIZ_CARDS_MIN,
    normalizeQuizTileSize, switchQuizView, resizeQuizTiles, cropSectionForm, groupRoundByForm,
  } from '../lib/reviewRounds.js'
  import { t, around, withText, localize, delocalize } from '../lib/i18n.svelte.js'
  import { graphemeAddress, ledTo, unslug } from '../lib/addresses.js'
  import { character as layerCharacter } from '../lib/layers.js'
  import { useSession } from '../lib/session.svelte.js'
  let { initialGrapheme = '', initialProduction = '', inspect } = $props()
  // The browser's reviewer id, which a form marking and its undo name like every other save.
  const session = useSession()
  let data = $state(null), items = $state([]), choices = $state({}), selected = $state({})
  let loaded = $state({}), failed = $state({}), suggestions = $state({}), contextSuggestions = $state({})
  // Crops the reader declined to judge: no choice, no request, not counted (see `skip`).
  let skipped = $state({})
  // What this round already saved, by crop: 'seen', 'skip' or 'flagged'. A saved crop stays in its
  // round, so going back through the history shows it, but it is never sent a second time.
  let recorded = $state({})
  // Crops of `recorded` whose save is kept on this device while writes are paused, not yet sent.
  let keptHere = $state({})
  // The form each crop was marked as here, which its tile shows in place of the label it was dealt with.
  let markedAs = $state({})
  // A crop this round already decided, by a saved problem or a form it was marked as, is not judged again.
  const decidedHere = id => recorded[id] === 'flagged' || recorded[id] === 'assigned'
  // Crops at least half of which have been on screen this round. Only these can be recorded as seen:
  // a batch that loaded below the fold was never looked at, and passing it would drop it for good.
  let viewed = $state({})
  // The round's grapheme (`U+306F`) and the characters it deals (は, ハ, 𛂞…).
  let grapheme = $state(''), members = $state([]), loading = $state(true), saving = $state(false), error = $state(''), errorStatus = $state(0)
  let categoryOpen = $state(false), search = $state(''), completed = $state(0), last = $state(null)
  let roundId = $state(crypto.randomUUID()), requestId = 0, closed = false
  let roundSeed = randomSeed()
  // The grapheme's character, and what the round is named by.
  const graphemeChar = $derived(grapheme ? graphemeText(grapheme) : ''), graphemeName = $derived(graphemeLabel(graphemeChar))
  // Already-confirmed and already-seen crops of the current grapheme, for comparison against the
  // round on screen. Loaded after the round itself, and a failure here never blocks the round.
  let references = $state([])
  let history = $state([]), historyIndex = $state(-1)
  let loadingMore = $state(false), hasMore = $state(false), nextCursor = $state(null), loadMoreFailed = $state(false)
  // A saved round says so, since the next character replaces it at once.
  let savedNotice = $state(''), savedTimer
  function announceSaved(count, label) {
    clearTimeout(savedTimer)
    savedNotice = t('quiz.roundSaved', { count, grapheme: label })
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
  // A round's material comes from its address; one the menu does not offer falls back to the default.
  const materialOf = value => MATERIALS.some(([option]) => option === value) ? value : 'not:printed/type'
  let production = $state(materialOf(untrack(() => initialProduction)))
  // The workflow state: selecting crops, or deciding them one at a time; and where in the selected crops the reader is.
  let step = $state('select'), at = $state(0)
  let suggestionsElement = $state(null)
  const keys = 'qwertyasdfgh'.split('')
  // The crops the classifier doubts (`suspect`), dealt alone when the reader asks. Each round keeps which way
  // it was dealt, since its cursor goes on in the same list. A visit that names no round takes the last choice.
  let onlySuspects = $state(false)
  const suspectsParam = only => only ? { suspects: 'only' } : {}
  // Turning the choice leaves the round as any way out does, its crops recorded; Back returns to it.
  async function toggleSuspects() {
    if (saving || loading || selection.length || step !== 'select') return
    if (!(await record())) return
    const only = !onlySuspects
    await load({ target: grapheme || null, only })
    if (onlySuspects === only) remember('atlas.quiz.suspects-only', only)
  }
  const selection = $derived(Object.keys(selected).filter(id => selected[id]))
  // A crop skipped in the review step stays selected so the reader can go back to it, but it is no
  // longer a problem to decide: whether the round still has one, and what moving on records, ask this.
  const openSelection = $derived(selection.filter(id => !skipped[id]))
  // A form marks the crops still to decide; one already given an answer keeps it.
  const markable = $derived(openSelection.filter(id => !choices[id]))
  const selectedItems = $derived(selection.map(id => items.find(i => i.id === id))
    .filter(item => item && !failed[item.id]))
  const queue = $derived(selectedItems)
  // Skips and issue changes shrink the queue, so the position that is *shown* is always clamped:
  // `at` may still point past the end for a moment, and a stage must never show a blank crop.
  const focusIndex = $derived(Math.min(at, Math.max(queue.length - 1, 0)))
  const current = $derived(step === 'select' ? null : queue[focusIndex] ?? null)
  // A character correction is chosen through the wrong-character card, so it shows as that card.
  const currentIssue = $derived(current ? choices[current.id]?.issue ?? null : null)
  const chosenIssues = choice => choice ? [choice.issue, ...(choice.bad_crop ? ['crop'] : [])] : []
  const currentIssues = $derived(current ? chosenIssues(choices[current.id]) : [])
  const answered = $derived(selectedItems.every(i => choices[i.id] || skipped[i.id]))
  const decided = $derived(remaining.filter(i => choices[i.id]?.verdict === 'wrong').length)
  // A selected crop still needs an explicit issue before it can be saved.
  const undecided = $derived(remaining.filter(i => selected[i.id] && !choices[i.id] && !skipped[i.id]).length)
  // Unavailable and skipped images cannot carry a decision.
  const remaining = $derived(items.filter(i => loaded[i.id] && !failed[i.id] && !skipped[i.id] && !decidedHere(i.id)))
  const settled = $derived(items.every(i => skipped[i.id] || failed[i.id] || (loaded[i.id] && !failed[i.id])))
  const ready = $derived(items.length > 0 && settled && remaining.length > 0)
  const exhausted = $derived(items.length > 0 && settled && !remaining.length)
  const rounds = $derived(roundGraphemes(data?.categories ?? []))
  const categories = $derived(rounds.filter(c => c.pending > 0 && (!search || c.members.some(m => m.label.includes(search)))))

  // Whether moving on would record something: a crop seen, or a crop skipped after it was seen.
  const recordable = $derived(!openSelection.length && (remaining.some(i => viewed[i.id] && !recorded[i.id])
    || items.some(i => skipped[i.id] && viewed[i.id] && !failed[i.id] && !recorded[i.id])))
  // A round or a crop changed under the reader: the error offers to reload the round in place.
  const stale = $derived(Boolean(error) && errorStatus === 409)
  const canNext = $derived(rounds.some(c => c.pending > 0 && c.label !== grapheme))
  function snapshot() {
    return $state.snapshot({ grapheme, members, items, choices, selected, skipped, recorded, keptHere, markedAs, suggestions, contextSuggestions,
      roundId, roundSeed, hasMore, nextCursor, onlySuspects, production, summary: data, entry })
  }
  function checkpoint() {
    if (historyIndex >= 0) history[historyIndex] = snapshot()
  }
  function restoreRound(round) {
    production = round.production; data = round.summary
    grapheme = round.grapheme; members = round.members; items = round.items; choices = round.choices; selected = round.selected
    skipped = round.skipped; recorded = round.recorded ?? {}; keptHere = round.keptHere ?? {}; markedAs = round.markedAs ?? {}; suggestions = round.suggestions; contextSuggestions = round.contextSuggestions
    roundId = round.roundId; roundSeed = round.roundSeed; hasMore = round.hasMore; nextCursor = round.nextCursor ?? null; onlySuspects = round.onlySuspects ?? false
    entry = round.entry ?? null
    loaded = {}; failed = {}; viewed = {}; step = 'select'; at = 0; error = ''; errorStatus = 0; categoryOpen = false
    anchor = null; formDone = null; formError = ''
  }
  function visit(index, { followed = false } = {}) {
    if (saving || loading || index < 0 || index >= history.length || index === historyIndex) return
    checkpoint()
    ++requestId; loadingMore = false
    historyIndex = index
    restoreRound($state.snapshot(history[index]))
    if (!followed) address('push')
  }
  /**
   * The address names the round on screen, `/review/U+85CF?production=handwritten`. Each round dealt or
   * returned to adds an entry to the browser's history, so Back steps through the rounds; a round dealt
   * again in place, and the first one, replace the entry there. The page does not navigate: the rounds,
   * and the choices made in them, stay.
   */
  let entry = null, addressed = false, followedState = untrack(() => page.state)
  function address(how) {
    // Back or Forward came while this round was being dealt or saved: the page follows that entry instead.
    if (page.state !== followedState) return
    const state = { round: { entry, grapheme, production, suspects: onlySuspects } }
    followedState = state
    ;(how === 'push' && addressed ? pushState : replaceState)(localize(roundAddress(grapheme, production, onlySuspects)), state)
    addressed = true
  }
  // Back and Forward bring the entry's round back, once nothing is being dealt or saved: from this page's
  // rounds when it still holds it, or dealt again (after a reload) from the grapheme and material the
  // entry names. An entry that names no round (a link to /review) is read from its address.
  $effect(() => {
    const state = page.state
    if (state === followedState || saving || loading) return
    followedState = state
    untrack(() => follow(state.round ?? locationRound()))
  })
  function locationRound() {
    const { path } = delocalize(location.pathname), key = path.startsWith('/review/') ? unslug(path.slice(8)) : null
    const query = new URLSearchParams(location.search)
    return { entry: null, grapheme: key ?? '', production: query.get('production') ?? '',
      suspects: query.get('suspects') === 'only' || !key && stored('atlas.quiz.suspects-only', false) === true }
  }
  function follow(round) {
    if (round.entry && round.entry === entry) return
    const index = round.entry ? history.findIndex(each => each.entry === round.entry)
      : history.findLastIndex(each => each.grapheme === round.grapheme && each.production === materialOf(round.production)
        && each.onlySuspects === Boolean(round.suspects))
    if (index >= 0 && index === historyIndex) address('replace')
    else if (index >= 0) visit(index, { followed: true })
    else load({ target: round.grapheme || null, scope: materialOf(round.production), how: 'replace', only: Boolean(round.suspects) })
  }
  async function chooseCategory(target) {
    categoryOpen = false
    if (target === grapheme || saving) return
    if (!(await record())) return
    const previous = history.findLastIndex(round => round.grapheme === target && round.production === production && round.onlySuspects === onlySuspects)
    if (previous >= 0) visit(previous)
    else load({ target })
  }
  /** Deals a round: of `target`, or the next. A target's key may have been a grapheme's and be a member's since
   *  (one an address or a kept round names): its grapheme's round is dealt. */
  async function load({ target = null, replace = false, scope = production, seed: dealt = null, how = replace ? 'replace' : 'push', only = onlySuspects } = {}) {
    checkpoint()
    // A round dealt again in place keeps its history entry.
    const kept = replace ? entry : null
    const id = ++requestId
    loading = true; loadingMore = false; error = ''; errorStatus = 0; categoryOpen = false
    try {
      const [summary, named] = await Promise.all([
        catalogue({ purpose: 'review', production: scope, limit: 0, state: 'pending', ...suspectsParam(only) }),
        target ? layerCharacter(target, 'none').then(ledTo).catch(() => target) : target])
      if (closed || id !== requestId) return
      // A summary of suspects alone counts for a round it deals, and not for the one on screen.
      if (!only || !grapheme) data = summary
      const epochKey = 'atlas.review-epoch'
      if (stored(epochKey, null) !== (summary.review_epoch ?? null)) {
        last = null; completed = 0; history = []; historyIndex = -1
        remember('atlas.last-round', null)
        remember(epochKey, summary.review_epoch ?? null)
      }
      // Crops of rounds kept on this device read as pending until they are sent, and are not dealt again.
      const away = await keptCrops()
      // A grapheme whose count promised crops that cannot be dealt gives way to the next one.
      const seed = dealt ?? randomSeed(), available = roundGraphemes(summary.categories).filter(c => c.pending > 0)
      const round = await dealtRound(available, named,
        left => nextGrapheme(left, scope === production ? grapheme : '', history.filter(r => r.production === scope), randomSeed()),
        key => firstCrops(cursor => catalogue({ purpose: 'review', production: scope, grapheme: key, state: 'pending', limit: ROUND_BATCH, seed,
          ...suspectsParam(only), ...(cursor ? { cursor } : {}) }), away), () => closed || id !== requestId)
      if (closed || id !== requestId) return
      const { chosen, result } = round
      // Opened on suspects alone, or taken to a material, with none to deal, a round deals the rest.
      if (!chosen && only && (!grapheme || scope !== production)) return await load({ target, replace, scope, seed: dealt, how, only: false })
      if (!chosen) {
        if (scope !== production) {
          restoreRound({ grapheme: '', members: [], items: [], choices: {}, selected: {}, skipped: {}, suggestions: {},
            contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: randomSeed(), hasMore: false,
            onlySuspects: only, production: scope, summary })
          historyIndex = -1
          address(how)
        } else {
          if (!grapheme) items = []
          else { error = t(only ? 'quiz.suspect.none' : 'quiz.noOtherCharacters'); errorStatus = 0 }
          // Asked for by an address, the address now names the round still on screen.
          if (how === 'replace') address('replace')
        }
        return
      }
      restoreRound({ grapheme: chosen, members: result.grapheme?.members ?? [graphemeText(chosen)], items: arranged(numbered(unique(result.items), 0, graphemeText(chosen))), choices: {}, selected: {}, skipped: {},
        suggestions: {}, contextSuggestions: {}, roundId: crypto.randomUUID(), roundSeed: seed,
        hasMore: Boolean(result.next), nextCursor: result.next ?? null, onlySuspects: only,
        production: scope, summary })
      entry = kept ?? crypto.randomUUID()
      address(how)
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
    // Continue from the cursor the last load handed back: a page goes on after the last crop it dealt, so
    // reviews saved meanwhile move nothing.
    let cursor = nextCursor, additions = [], more = false
    try {
      const batchLimit = Math.min(MORE_BATCH, roundLimit - items.length)
      // A crop of a kept round is left out as one on screen is.
      for (const kept of await keptCrops()) seen.add(kept)
      // A page asks for no more than the batch has room for, since the cursor moves past every crop dealt.
      while (additions.length < batchLimit && cursor) {
        const room = batchLimit - additions.length
        const result = await catalogue({ purpose: 'review', production, grapheme, state: 'pending', limit: room, cursor, seed: roundSeed, ...suspectsParam(onlySuspects) })
        if (closed || id !== requestId || round !== roundId) return
        const fresh = unique(result.items).filter(item => !seen.has(item.id))
        additions.push(...fresh)
        fresh.forEach(item => seen.add(item.id))
        cursor = result.next ?? null
      }
      more = Boolean(cursor)
      // Added crops follow the ones already on screen, so nothing the reviewer is looking at moves.
      items = [...items, ...arranged(numbered(additions, items.length, graphemeChar))]; hasMore = more; nextCursor = cursor
    } catch (e) { if (id === requestId) { error = e.message; errorStatus = e.status ?? 0; loadMoreFailed = true } }
    finally { if (id === requestId) loadingMore = false }
  }
  /**
   * The reference strip for the current grapheme: crops already confirmed, then crops already
   * seen. Reloaded whenever the round changes; a failure here just leaves the strip empty, since
   * it is a comparison aid, not a decision the round depends on.
   */
  async function loadReferences() {
    const target = grapheme, scope = production, round = roundId
    if (!target) { references = []; return }
    try {
      const [checked, seen] = await Promise.all([
        catalogue({ purpose: 'review', production: scope, grapheme: target, state: 'checked', limit: REFERENCE_LIMIT, seed: roundSeed }),
        catalogue({ purpose: 'review', production: scope, grapheme: target, state: 'seen', limit: REFERENCE_LIMIT, seed: roundSeed }),
      ])
      if (closed || round !== roundId) return
      references = mergeReferences(checked.items ?? [], seen.items ?? [])
    } catch { if (!closed && round === roundId) references = [] }
  }
  $effect(() => { void roundId; loadReferences() })
  const decidable = $derived(remaining)
  const available = $derived(decidable.length)

  // Step one: choose the crops that are wrong, or that the form bar marks as one form. A selection is
  // not a verdict, so nothing is assigned here, and a crop taken out of the selection gives up the
  // answer it had. Shift-click selects every crop from the last one clicked (stored by id so loading
  // more crops into an earlier form section never shifts the anchor).
  let anchor = null
  const selectable = id => loaded[id] && !failed[id] && !decidedHere(id) && !skipped[id]
  function toggle(id, event) {
    if (saving || loading || !loaded[id] || failed[id] || decidedHere(id)) return
    const index = ordered.findIndex(i => i.id === id)
    const anchorIndex = anchor ? ordered.findIndex(i => i.id === anchor) : -1
    if (event?.shiftKey && anchorIndex >= 0 && index >= 0) {
      const [from, to] = anchorIndex < index ? [anchorIndex, index] : [index, anchorIndex]
      const range = ordered.slice(from, to + 1).map(i => i.id).filter(selectable)
      selected = { ...selected, ...Object.fromEntries(range.map(each => [each, true])) }
      anchor = id; at = 0; return
    }
    anchor = id
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
    selected = {}; at = 0; anchor = null
  }
  /**
   * Mark crops as a form, as Explore's bar marks a selection: a batch correction each 144 crops, undone
   * together. The bar marks the selection (`markSelection`), and the crop on show is marked alone
   * (`markCurrent`), so both end the same way: the tile names the form and reads as saved. A form of the
   * round's grapheme names the crop's character; any other (a variant, a derived shape, a description)
   * leaves each crop the character it is written as, which the marking confirms. `claims` are the forms
   * claimed on the crops beforehand (`markClaimed`), by crop, `{ form, replaced, before, crop }`, which an
   * undo takes back. A crop already checked as its character has nothing to change; a refused crop
   * leaves the round, which deals it again as it now stands.
   */
  // A marking and its undo hold the round as a save does: nothing moves to another round meanwhile.
  let formError = $state(''), formDone = $state(null)
  // The style chosen in the selection bar: given with the next form, or alone.
  let formStyle = $state(null)
  /** Give `chosen` the style, after what the same press gave them (`done`); the crops keep their place in the round
   *  and take the revision the style left. Crops just corrected are styled as they stand. */
  async function styleChosen(chosen, style, done = null) {
    if (!style || !chosen.length) return
    let styled = [], refused = [], kept = []
    saving = true
    try { ({ styled, refused, kept } = await styleCrops(chosen, style)) }
    catch (e) { styled = e.styled ?? []; refused = e.refused ?? []; kept = e.kept ?? []; formError = e.status === 429 ? t('bulk.tooMany') : e.message }
    finally { saving = false }
    if (refused.length && !formError) formError = t('bulk.refused', { count: refused.length })
    if (kept.length && !formError) formError = t('client.pausedKept')
    const now = Object.fromEntries(styled.map(({ after }) => [after.id, styleOf(after)]))
    items = items.map(i => now[i.id] ? { ...i, ...now[i.id] } : i)
    if (styled.length) formDone = done ? { ...done, styled, style } : { kind: 'style', styled, style, count: styled.length }
  }
  /** The style chosen in the bar, given to the selection alone. */
  function styleSelection() {
    if (saving || loading || step !== 'select') return
    formError = ''
    return styleChosen(markable.map(id => items.find(i => i.id === id)).filter(i => i && selectable(i.id)), formStyle)
  }
  const CORRECTION_BATCH = 144
  async function assignForm(form, chosen, claims = {}, relabel = false) {
    if (!chosen.length) return
    const named = relabel || members.includes(form)
    const characterOf = i => named ? form : i.written_character || i.label
    const crops = chosen.filter(i => !((i.written_character || i.label) === characterOf(i) && i.state === 'checked'))
    // One run of batches for each character the crops are corrected to.
    const byCharacter = new Map()
    for (const i of crops) byCharacter.set(characterOf(i), [...byCharacter.get(characterOf(i)) ?? [], i])
    const batches = [...byCharacter].flatMap(([character, part]) =>
      correctionBatches(character, part, CORRECTION_BATCH).map(batch => ({ ...batch, character })))
    let kept = chosen.length - crops.length, refused = [], answered = [], waiting = []
    const saved = [], before = {}, after = {}
    saving = true; formError = ''
    try {
      ({ answered, kept: waiting } = await sendBatches(batches, ({ id, character, crops: part }) =>
        request('/atlas/corrections', { id, client_id: session.state.clientId, character,
          crops: part.map(i => ({ id: i.id, revision: i.revision, ...pixels(i) })) })))
    } catch (e) {
      answered = e.answered ?? []; waiting = e.kept ?? []
      refused = (e.targets ?? []).map(target => target.id)
      formError = refused.length ? t('bulk.refused', { count: refused.length }) : e.status === 429 ? t('bulk.tooMany') : e.status === 409 ? t('bulk.changed') : e.message
    } finally { saving = false }
    for (const { item: { id, character, crops: part }, result } of answered) {
      const byId = Object.fromEntries((result.results ?? []).map(r => [r.target_id, r]))
      saved.push({ id, ids: part.filter(i => byId[i.id]).map(i => i.id) })
      kept += (result.unchanged ?? []).length
      for (const i of part) if (byId[i.id]) {
        before[i.id] = i
        after[i.id] = { ...i, label: character, char: character, written_character: character, identity_status: 'assigned',
          state: byId[i.id].state ?? 'checked', revision: byId[i.id].revision ?? i.revision + 1 }
      }
    }
    // Batches kept while writes are paused go once they resume: their crops are decided here meanwhile.
    const waited = waiting.flatMap(batch => batch.crops)
    if (waited.length) {
      markRecorded(waited, 'assigned'); markAs(waited, form)
      for (const batch of waiting) markKept(batch.crops, keyOf('/atlas/corrections', batch))
      if (!formError) formError = t('client.pausedKept')
    }
    const unchanged = chosen.filter(i => !crops.includes(i))
    const handled = [...Object.keys(after), ...waited.map(i => i.id), ...unchanged.map(i => i.id), ...refused]
    // A claimed form holds on a crop that was already checked, so that crop is marked as well.
    const claimed = unchanged.filter(i => claims[i.id])
    if (saved.length || claimed.length) {
      items = items.map(i => after[i.id] ?? i)
      markRecorded([...Object.values(after), ...claimed], 'assigned'); markAs([...Object.values(after), ...claimed], form)
      formDone = { batches: saved, count: Object.keys(after).length + claimed.length, char: form, before, kept,
        claims: Object.fromEntries([...Object.keys(after), ...claimed.map(i => i.id)].filter(id => claims[id]).map(id => [id, claims[id]])) }
    }
    selected = without(selected, handled); choices = without(choices, handled); anchor = null
    for (const id of refused) drop(id)
  }
  /**
   * Claim `form` on `chosen`, a batch of picks each 48 crops (`markForms`), as the inspector's form bar
   * claims it on one crop, then mark the crops it claimed as it. A correction that did not go takes its
   * crop's claim back, so the crop stands as it was. A crop whose claim is kept while writes are paused
   * leaves the selection, and the claim goes once they resume. Answers the error a claim stopped with.
   */
  async function markClaimed(form, chosen) {
    if (!chosen.length) return null
    let marked = [], kept = [], failure = null
    saving = true
    try { ({ marked, kept } = await markForms(chosen, form)) }
    catch (e) { marked = e.marked ?? []; kept = e.kept ?? []; failure = e }
    finally { saving = false }
    const claims = Object.fromEntries(marked.map(({ crop, replaced }) => {
      const before = chosen.find(i => i.id === crop.id)
      return [crop.id, { form, replaced, before, crop: { ...before, ...crop } }]
    }))
    await assignForm(form, Object.values(claims).map(claim => claim.crop), claims)
    const lost = Object.values(claims).filter(claim => !markedAs[claim.crop.id])
    if (lost.length) await unmarkForms(lost).catch(() => {})
    const waited = kept.map(crop => crop.id)
    selected = without(selected, waited); choices = without(choices, waited)
    if (waited.length && !formError) formError = t('client.pausedKept')
    return failure
  }
  /** Mark the selection as `form`: a character by correction alone, a description claimed on each
   *  crop first. */
  async function markSelection(form) {
    if (saving || loading || step !== 'select') return
    const chosen = markable.map(id => items.find(i => i.id === id)).filter(i => i && selectable(i.id)), style = formStyle
    if (members.includes(form)) await assignForm(form, chosen)
    // A character found by search is what the crops are written as, as in the collection view's bar.
    else if (!isDescription(form)) await assignForm(form, chosen, {}, true)
    else {
      const failure = await markClaimed(form, chosen)
      if (failure) formError = failure.status === 429 ? t('bulk.tooMany') : failure.status === 409 ? t('bulk.changed') : failure.message
    }
    // The style goes to the crops the form was given, as they now stand.
    const marked = chosen.filter(i => markedAs[i.id] === form).map(i => ({ ...i, revision: null }))
    if (style && !formError) await styleChosen(marked, style, formDone)
  }
  /** Confirm every selected crop as the character it is labelled with ("Looks right"), matching Explore's BulkBar. */
  async function confirmSelection() {
    if (saving || loading || step !== 'select') return
    const chosen = markable.map(id => items.find(i => i.id === id)).filter(i => i && selectable(i.id)), style = formStyle
    if (!chosen.length) return
    const plan = confirmationPlan(chosen)
    const byCharacter = new Map()
    for (const i of plan.crops) {
      const char = confirmedCharacter(i)
      if (char) byCharacter.set(char, [...byCharacter.get(char) ?? [], i])
    }
    const batches = [...byCharacter].flatMap(([character, part]) =>
      correctionBatches(character, part, ROUND_MAX).map(batch => ({ ...batch, character })))
    let kept = plan.report.filter(entry => entry.reason === 'checked').length
    let refused = [], answered = [], waiting = []
    const saved = [], before = {}, after = {}
    saving = true; formError = ''
    try {
      if (batches.length) {
        ({ answered, kept: waiting } = await sendBatches(batches, ({ id, character, crops: part }) =>
          request('/atlas/corrections', { id, client_id: session.state.clientId, character,
            crops: part.map(i => ({ id: i.id, revision: i.revision, ...pixels(i) })) })))
      }
    } catch (e) {
      answered = e.answered ?? []; waiting = e.kept ?? []
      refused = (e.targets ?? []).map(target => target.id)
      formError = refused.length ? t('bulk.refused', { count: refused.length }) : e.status === 429 ? t('bulk.tooMany') : e.status === 409 ? t('bulk.changed') : e.message
    } finally { saving = false }
    for (const { item: { id, crops: part }, result } of answered) {
      const byId = Object.fromEntries((result.results ?? []).map(r => [r.target_id, r]))
      const ids = part.filter(i => byId[i.id]).map(i => i.id)
      if (ids.length) saved.push({ id, ids })
      kept += (result.unchanged ?? []).length
      for (const i of part) if (byId[i.id]) {
        before[i.id] = i
        after[i.id] = { ...i, state: byId[i.id].state ?? 'checked', revision: byId[i.id].revision ?? i.revision + 1 }
      }
    }
    const waited = waiting.flatMap(batch => batch.crops)
    if (waited.length) {
      markRecorded(waited, 'assigned')
      for (const batch of waiting) markKept(batch.crops, keyOf('/atlas/corrections', batch))
      if (!formError) formError = t('client.pausedKept')
    }
    if (saved.length) {
      items = items.map(i => after[i.id] ?? i)
      markRecorded(Object.values(after), 'assigned')
      formDone = { kind: 'confirm', batches: saved, count: Object.keys(after).length, before, kept, claims: {} }
    }
    const unchanged = answered.flatMap(({ result }) => result.unchanged ?? [])
    const handled = [...Object.keys(after), ...waited.map(i => i.id), ...plan.report.map(e => e.id), ...unchanged, ...refused]
    selected = without(selected, handled); choices = without(choices, handled); anchor = null
    for (const id of refused) drop(id)
    if (style && !formError && saved.length) await styleChosen(Object.values(after).map(i => ({ ...i, revision: null })), style, formDone)
  }
  async function undoForm() {
    let done = formDone
    if (!done || saving) return
    // The style given last goes first, each crop back to its own style or the one it inherits.
    if (done.styled?.length) {
      saving = true; formError = ''
      let back, failed = null
      try { back = await unstyleCrops(done.styled) }
      catch (e) { back = { styled: e.styled ?? [], refused: e.refused ?? [], kept: e.kept ?? [] }; failed = e }
      finally { saving = false }
      // The crops taken back keep the revision it left them at, so the round's answers for them are taken.
      const now = Object.fromEntries(back.styled.map(({ after }) => [after.id, styleOf(after)]))
      items = items.map(i => now[i.id] ? { ...i, ...now[i.id] } : i)
      const left = done.styled.filter(({ after }) => !now[after.id])
      if (left.length) { formDone = { ...done, styled: left }; formError = failed ? failed.message : back.kept.length ? t('client.pausedKept') : t('bulk.refused', { count: left.length }); return }
      if (done.kind === 'style') { formDone = null; return }
      done = formDone = { ...done, styled: [] }
    }
    saving = true; formError = ''
    const left = [...done.batches], claims = { ...done.claims }
    const gone = batch => left.splice(left.indexOf(batch), 1)
    try {
      // Newest batch first. An undo is a change of its own, so a batch's crops are read again for the
      // revision they now have as soon as it is undone; a batch that fails leaves only itself and the
      // older ones to undo. An undo kept while writes are paused goes once they resume.
      const { kept: waiting } = await sendEach([...done.batches].reverse(), async batch => {
        await request(`/atlas/corrections/${batch.id}/undo`, { client_id: session.state.clientId })
        gone(batch)
        const now = Object.fromEntries(await Promise.all(batch.ids.map(async id => {
          const i = done.before[id]
          return [id, { ...i, ...await character(id) }]
        })))
        items = items.map(i => now[i.id] ?? i)
        unmark(batch.ids)
      })
      waiting.forEach(gone)
      if (waiting.length) formError = t('client.pausedKept')
      // The claimed forms are taken back once their crops' corrections are, a batch of picks at a time:
      // the reader's earlier claim returns. A batch that fails leaves its crops for the next Undo.
      if (!left.length && Object.keys(claims).length) {
        const sent = Object.entries(claims).map(([id, claim]) => ({ crop: items.find(i => i.id === id) ?? claim.crop, replaced: claim.replaced }))
        let marked = [], paused = [], failure = null
        try { ({ marked, kept: paused } = await unmarkForms(sent)) }
        catch (e) { marked = e.marked ?? []; paused = e.kept ?? []; failure = e }
        const back = failure ? [...marked.map(({ crop }) => crop.id), ...paused.map(crop => crop.id)] : Object.keys(claims)
        const now = new Map(marked.map(({ crop }) => [crop.id, crop.form]))
        items = items.map(i => now.has(i.id) ? { ...i, form: now.get(i.id) } : i)
        for (const id of back) delete claims[id]
        unmark(back)
        if (failure) throw failure
        if (paused.length) formError = t('client.pausedKept')
      }
    } catch (e) { (e.kept ?? []).forEach(gone); formError = e.message }
    finally {
      saving = false
      const count = left.reduce((n, batch) => n + batch.ids.length, 0) || Object.keys(claims).length
      formDone = left.length || Object.keys(claims).length ? { ...done, batches: left, claims, count } : null
    }
  }
  /** A marking taken back: the crops are undecided again and back among the selected problems. */
  function unmark(ids) {
    recorded = without(recorded, ids); markedAs = without(markedAs, ids)
    selected = { ...selected, ...Object.fromEntries(ids.map(id => [id, true])) }
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
    if (issue === previous) return choice
    const correction = choice?.character || choice?.correction
    if (!correction) return { correction: null, character: null }
    if (!offersSuggestions(issue)) return { correction: null, character: null }
    const fits = issue === 'merged' ? !isSingle(correction) : isSingle(correction)
    if (!fits) return { correction: null, character: null }
    return issue === 'merged' ? { correction, character: null } : { correction: null, character: correction }
  }
  /** The answer for one crop, from step two: its own problems, never shared ones. Pressing a chosen card takes it back. */
  function assign(id, pressed) {
    if (saving) return
    const next = toggleIssue(chosenIssues(choices[id]), pressed)
    if (next.length) {
      const { bad_crop: _, ...kept } = compatible(choices[id], next[0], choices[id]?.issue ?? null)
      choices = { ...choices, [id]: { verdict: 'wrong', ...kept, ...issueFields(next) } }
    } else choices = without(choices, [id])
    if (skipped[id]) skipped = without(skipped, [id])
    if (pressed === next[0] && offersSuggestions(pressed)) suggest(items.find(i => i.id === id))
    error = ''; errorStatus = 0
  }
  /** The classifier's reading taken as what a doubted crop is written as: the crop is chosen as a wrong character
   *  with that one, and pressed again it is neither. */
  const reading = item => item.suspect?.reads_as ?? null
  const taken = item => reading(item) !== null && choices[item.id]?.character === reading(item)
  async function takeReading(item, button) {
    if (saving || loading || step !== 'select' || !selectable(item.id)) return
    const { id } = item
    anchor = id; at = 0
    const chosen = chosenIssues(choices[id]), bad = chosen.includes('crop')
    if (taken(item)) {
      if (bad) choices = { ...choices, [id]: { verdict: 'wrong', character: null, correction: null, noneSelected: false, ...issueFields(['crop']) } }
      else { choices = without(choices, [id]); selected = { ...selected, [id]: false } }
    } else {
      selected = { ...selected, [id]: true }
      choices = { ...choices, [id]: { verdict: 'wrong', character: reading(item), correction: null, noneSelected: false, ...issueFields(bad ? ['character', 'crop'] : ['character']) } }
    }
    error = ''; errorStatus = 0
    // A tile dealt among the others has its reading only while it is selected.
    await tick()
    if (!button.isConnected) document.querySelector(`.quiz-tile[data-unit="${CSS.escape(id)}"] .quiz-choice`)?.focus({ preventScroll: true })
  }
  /** Any other character: the crop's own step, with its candidates open under the wrong-character card. A crop with a
   *  main problem opens as it stands, and one left without a character is given no answer. */
  let opened = null
  async function correctOne(item) {
    if (saving || loading || step !== 'select' || !selected[item.id]) return
    const { id } = item, chosen = chosenIssues(choices[id])
    if (!chosen.some(issue => issue !== 'crop')) { assign(id, 'character'); opened = id }
    jump(queue.findIndex(i => i.id === id))
    await tick()
    if (current?.id === id) greetSuggestions(suggestionsElement, { focus: true })
  }
  /** Choose the current crop's issue. The candidates, when the issue has any, open under the cards. */
  async function assignCurrent(issue) {
    if (!current) return
    const id = current.id
    assign(current.id, issue)
    if (!offersSuggestions(issue) || choices[id]?.issue !== issue) return
    await tick()
    if (current?.id === id) greetSuggestions(suggestionsElement, { focus: true })
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
  const pixels = i => cropPixels(i)
  /** Take a crop out of the round: a review saved elsewhere has decided it. */
  function drop(id) {
    items = items.filter(i => i.id !== id)
    choices = without(choices, [id]); selected = without(selected, [id]); skipped = without(skipped, [id])
  }
  /**
   * Open a crop in the inspector, the same editor and the same saves as everywhere else; its arrows step
   * through the round. What it writes comes back here: a field (style, reading, word) changes the tile,
   * and a save decides the crop, which leaves the selection and the problems and shows as saved.
   */
  function inspectChoice(item, opening = null) {
    inspect(item.id, ordered, inspected, opening)
  }
  async function inspected(id, result, how) {
    const item = items.find(i => i.id === id), round = roundId
    if (!item) return
    // A crop skipped in the editor was seen there, so the round records the skip.
    if (how === 'skipped') { viewed = { ...viewed, [id]: true }; skip([id]); return }
    // A save decides the crop at once: it leaves the selection, the answers and the skips, and shows as saved.
    if (how === 'saved') {
      choices = without(choices, [id]); selected = without(selected, [id]); skipped = without(skipped, [id])
      markRecorded([item], (result?.results?.[0]?.state ?? result?.state) === 'flagged' ? 'flagged' : 'assigned')
      // The last crop of the issue step decided here leaves nothing to show there.
      if (step !== 'select' && !queue.length) back()
    }
    // A crop's updated record is read again, as Explore reads it.
    const record = await character(id).catch(() => null)
    // The round may have moved on meanwhile; the tile it left is no longer on screen.
    if (closed || roundId !== round || !record) return
    items = items.map(i => i.id === id ? { ...i, ...record } : i)
    if (how === 'saved') markRecorded([item], record.state === 'flagged' ? 'flagged' : 'assigned')
  }
  /**
   * Mark the crop on show as one of its grapheme's forms, as the bar marks a selection: the crop is
   * decided, leaves the problems to answer, and the step goes on to the next one. A form outside the
   * grapheme (a variant, a derived shape, a description) leaves the crop the character it is written as,
   * which the marking confirms.
   */
  async function markCurrent(form) {
    if (!current || form == null || saving || loading) return
    const item = current
    // The form is claimed first, as the inspector's form bar claims it; the correction then names a form
    // of the grapheme as the crop's character, or confirms the character any other form is written as.
    error = ''; errorStatus = 0
    const failure = await markClaimed(form, [item])
    if (failure) { error = failure.message; errorStatus = failure.status ?? 0; return }
    await tick()
    if (!queue.length) back()
  }
  function chooseSuggestion(id, value, noneSelected = false) {
    const current = choices[id]
    if (!current) return
    // One character names the character, except under joined characters, which picks them in turn.
    choices = { ...choices, [id]: value && isSingle(value) && current.issue !== 'merged'
      ? { verdict: 'wrong', issue: 'character', character: value, correction: null, noneSelected: false, ...(current.bad_crop ? { bad_crop: true } : {}) }
      // Typed characters belong to a joined crop; on any other issue they name nothing.
      : { ...current, character: null, correction: current.issue === 'merged' ? value : null, noneSelected } }
  }
  function reviewSelected() {
    // No `answered` guard here: the reader has just selected the crops and has not been asked for a
    // problem yet, so this is the way in. The guard belongs to the step that leaves the issue stage.
    if (!selection.length) return
    jump(0)
  }
  function back() {
    const left = choices[opened]
    if (left?.issue === 'character' && !left.character && !left.noneSelected) assign(opened, 'character')
    opened = null
    step = 'select'
    at = Math.min(at, Math.max(queue.length - 1, 0))
    error = ''; errorStatus = 0
  }
  function jump(i) {
    at = Math.max(0, Math.min(queue.length - 1, i))
    const item = queue[at], choice = choices[item?.id]
    step = 'issue'
    if (item && offersSuggestions(choice?.issue) && (!suggestions[item.id] || !contextSuggestions[item.id])) suggest(item)
    error = ''; errorStatus = 0
  }
  const move = delta => jump(focusIndex + delta)

  /** The action the primary button offers, and the one Ctrl/Cmd+Enter performs. */
  function primary() {
    if (saving || loading || loadingMore) return
    if (!openSelection.length) { pass(); return }
    if (step === 'select') { if (answered) submit(); else reviewSelected(); return }
    if (!current || !(choices[current.id] || skipped[current.id])) return
    if (focusIndex < queue.length - 1) { move(1); return }
    if (!answered) { jump(selectedItems.findIndex(i => !choices[i.id] && !skipped[i.id])); return }
    submit()
  }
  // A crop recorded as seen or skipped, as it was shown: the site records nothing for one that changed since.
  const observation = i => ({ id: i.id, revision: i.revision, ...pixels(i), image: i.image })
  /** The crops this round skipped. A skip is recorded against the reviewer, not as a decision: other
   * reviewers are dealt the crop first, and it comes back to this one only after a rest. */
  function skippedCrops() {
    return items.filter(i => skipped[i.id] && !failed[i.id] && viewed[i.id] && !recorded[i.id]).map(observation)
  }
  // A round the site refused is reported in the reader's language; the service's messages are English.
  const roundError = e => e.status === 409 ? t('quiz.roundChanged') : e.code === 'paused' ? t('client.paused') : t('quiz.roundNotSaved')
  // A round kept on this device while writes are paused counts as done here, and the next one is dealt:
  // it is sent once writes resume. Resolves true when kept.
  const keptWhilePaused = sent => sent.then(() => false, e => { if (e?.kept) return true; throw e })
  // A tab open across a deployment runs the page it loaded, and the site may refuse what that page sends.
  // A refusal while a newer page is out offers the reload (the notice above the round) in place of the error.
  const outdated = async e => e.status >= 400 && e.status < 500 && (updated.current || await updated.check())
  /**
   * Reload into the newer page with this round: its grapheme, material and deal, and the choices made in
   * it, kept for this tab and taken back once the round is dealt again.
   */
  const CARRIED = 'atlas.quiz.carried'
  function reloadKeepingRound() {
    try {
      sessionStorage.setItem(CARRIED, JSON.stringify({ at: Date.now(), grapheme, production, suspects: onlySuspects, seed: roundSeed,
        ...$state.snapshot({ choices, selected, skipped }) }))
    } catch { /* The page reloads without the choices. */ }
    location.reload()
  }
  function carried() {
    // Only the reload that kept it: a round left behind by a reload that went elsewhere, or copied into a
    // duplicated tab, is not dealt in place of the one asked for later.
    try { const round = JSON.parse(sessionStorage.getItem(CARRIED) ?? 'null'); sessionStorage.removeItem(CARRIED); return round && Date.now() - round.at < 2 * 60 * 1000 ? round : null }
    catch { return null }
  }
  async function resume(round) {
    await load({ target: round.grapheme || null, scope: materialOf(round.production), seed: round.seed, how: 'replace', only: Boolean(round.suspects) })
    // Only the crops dealt again: a crop decided elsewhere meanwhile has left the round.
    const dealt = new Set(items.map(item => item.id))
    const here = map => Object.fromEntries(Object.entries(map ?? {}).filter(([id]) => dealt.has(id)))
    choices = here(round.choices); selected = here(round.selected); skipped = here(round.skipped)
  }
  function markAs(crops, form) { markedAs = { ...markedAs, ...Object.fromEntries(crops.map(crop => [crop.id, form])) } }
  function markRecorded(crops, how) { recorded = { ...recorded, ...Object.fromEntries(crops.map(crop => [crop.id, how])) } }
  /** Mark `crops` as decided by the save kept under `key`, until it is sent or refused. */
  function markKept(crops, key) { keptHere = { ...keptHere, ...Object.fromEntries(crops.map(crop => [crop.id, key])) } }
  // A crop decided here reads as saved once its kept save is sent, and as not saved if the site refused it.
  function recordedLabel(id) {
    const state = keptHere[id] ? keptState(keptHere[id]) : 'sent'
    return state === 'refused' ? t('app.notSaved') : state === 'kept' ? t('app.keptHere') : '✓ ' + t('app.saved')
  }
  async function submit() {
    if (saving || loadingMore || !ready) return
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
    const seen = remaining.filter(i => !flagged.has(i.id) && viewed[i.id] && !recorded[i.id]).map(observation)
    const passed = skippedCrops()
    if (!answers.length && !seen.length && !passed.length) { await load(); return }
    saving = true; error = ''; errorStatus = 0
    try {
      const kept = await keptWhilePaused(request('/atlas/rounds', { id: roundId, grapheme, answers, seen, skipped: passed }))
      last = { id: roundId, count: answers.length, grapheme, production, suspects: onlySuspects }
      remember('atlas.last-round', last); completed += answers.length
      if (!kept) announceSaved(answers.length + seen.length + passed.length, graphemeName)
      markRecorded(answers, 'flagged'); markRecorded(seen, 'seen'); markRecorded(passed, 'skip')
      if (kept) markKept([...answers, ...seen, ...passed], keyOf('/atlas/rounds', { id: roundId }))
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      await load()
    } catch (e) { if (!(await outdated(e))) { error = roundError(e); errorStatus = e.status ?? 0 } }
    finally { saving = false }
  }
  // Moving on from a round with nothing flagged: the crops it showed were seen, so they are recorded
  // as seen before the next round is dealt. Skipped crops are recorded as skipped; crops that failed
  // to load were not seen at all.
  /** Record what a round with nothing selected showed: the crops seen, and the crops skipped. Every
   * way out of such a round goes through here, so none of them drops the round's record. */
  async function record() {
    const seen = remaining.filter(i => viewed[i.id] && !recorded[i.id]).map(observation)
    const passed = skippedCrops()
    if ((!seen.length && !passed.length) || openSelection.length) return true
    saving = true; error = ''; errorStatus = 0
    try {
      const kept = await keptWhilePaused(request('/atlas/rounds', { id: roundId, grapheme, seen, skipped: passed }))
      last = { id: roundId, count: 0, grapheme, production, suspects: onlySuspects }
      remember('atlas.last-round', last)
      if (!kept) announceSaved(seen.length + passed.length, graphemeName)
      markRecorded(seen, 'seen'); markRecorded(passed, 'skip')
      if (kept) markKept([...seen, ...passed], keyOf('/atlas/rounds', { id: roundId }))
      choices = {}; selected = {}; step = 'select'; at = 0; roundId = crypto.randomUUID()
      return true
    } catch (e) { if (!(await outdated(e))) { error = roundError(e); errorStatus = e.status ?? 0 }; return false }
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
      const sent = request(`/atlas/rounds/${last.id}/undo`, {})
      const kept = await keptWhilePaused(sent)
      // Crops a publication changed since stay as published; the round keeps its count for them.
      const { refused } = kept ? { refused: 0 } : undoOutcome(await sent)
      // A round stored by an older version may name a scope the menu no longer offers.
      const target = last.grapheme, only = Boolean(last.suspects), scope = MATERIALS.some(([value]) => value === last.production) ? last.production : production
      completed = Math.max(0, completed - Math.max(0, last.count - refused)); last = null
      remember('atlas.last-round', null); await load({ target, scope, only })
      if (refused) error = t('review.undoRefused', { count: refused })
    } catch (e) { error = e.message; errorStatus = e.status ?? 0 }
    finally { saving = false }
  }
  function keydown(e) {
    if (document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
    // The save is explicit: Ctrl/Cmd+Enter is the shortcut, and a bare Enter belongs to whatever
    // control has focus — choosing a crop or a suggestion must never post the round.
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); primary(); return }
    if (e.metaKey || e.ctrlKey || e.altKey) return
    // The character search's list and the IDS editor are their own: their keys never reach the round.
    if (e.target.closest?.('.character-search, .ids-composer')) return
    const control = e.target.closest('button, a, input, textarea, select, summary, [contenteditable="true"]')
    if (step === 'select') {
      if (e.key.startsWith('Arrow') && ordered.length) {
        const tiles = [...document.querySelectorAll('.quiz-grid .quiz-tile')]
        const byId = new Map(tiles.map(node => [node.dataset.unit, node]))
        const focusable = ordered
          .map(item => ({ item, node: byId.get(item.id), button: byId.get(item.id)?.querySelector('.quiz-choice') }))
          .filter(entry => entry.button && !entry.button.disabled)
        if (!focusable.length) return
        const currentTile = e.target.closest?.('.quiz-grid .quiz-tile')
        const index = currentTile ? focusable.findIndex(entry => entry.item.id === currentTile.dataset.unit) : -1
        const under = e.target.closest?.('.quiz-section')?.querySelector('.quiz-tile:has(.quiz-choice:not(:disabled))')
        const to = index < 0 ? Math.max(0, under ? focusable.findIndex(entry => entry.item.id === under.dataset.unit) : 0)
          : arrowTarget(focusable.map(entry => entry.node.getBoundingClientRect()), index, e.key)
        if (to != null) {
          e.preventDefault()
          focusable[to].button.focus()
          focusable[to].button.scrollIntoView({ block: 'nearest' })
        }
        return
      }
      const focusedChoice = e.target.closest?.('.quiz-grid .quiz-choice')
      if (focusedChoice) {
        const unitId = focusedChoice.closest('.quiz-tile')?.dataset.unit
        if (unitId && e.shiftKey && (e.key === ' ' || e.key === 'Enter')) {
          e.preventDefault(); toggle(unitId, { shiftKey: true }); return
        }
        if (unitId && !e.shiftKey && e.key.toLowerCase() === 'i') {
          const item = items.find(each => each.id === unitId)
          if (item) { e.preventDefault(); inspectChoice(item); return }
        }
      }
      const index = keys.indexOf(e.key.toLowerCase())
      if (index >= 0 && ordered[index] && !control) { e.preventDefault(); toggle(ordered[index].id) }
      // 1–0 mark the selection as the grapheme's forms, in the bar's order.
      const form = members['1234567890'.indexOf(e.key)]
      if (/^[0-9]$/.test(e.key) && form && markable.length) { e.preventDefault(); markSelection(form) }
      if (e.key === 'Escape') clearSelection()
      return
    }
    if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1); return }
    if (e.key === 'ArrowRight') { e.preventDefault(); move(1); return }
    if (e.key === 'Escape') { e.preventDefault(); back(); return }
    // The problem keys, Skip and None of these belong to the crop's review panel.
  }
  // A round is dealt as before; shown in shape order, the crops of one form sit together and a crop
  // unlike its neighbours stands out. `dealt` keeps the dealt order for switching back.
  let byShape = $state(stored('atlas.quiz.shape-order', true) !== false)
  // View mode ('cards' or 'compact' thumbnails), tile width, and grouping by written form (字樣).
  // Remembered in localStorage; restored on mount so server rendering matches initial client paint.
  let view = $state('cards')
  let tileSize = $state(QUIZ_CARDS_SIZE)
  let cardsSize = QUIZ_CARDS_SIZE, compactSize = QUIZ_COMPACT_SIZE
  let groupByForm = $state(true)
  const compact = $derived(view === 'compact')
  const sizeIndex = $derived(Math.max(0, QUIZ_TILE_SIZES.indexOf(tileSize)))
  const gridGap = $derived(compact ? (tileSize <= 60 ? 5 : tileSize <= 96 ? 6 : 8) : (tileSize <= 124 ? 10 : 12))
  const formSections = $derived(groupRoundByForm(items, members, graphemeChar))
  const sectioned = $derived(groupByForm && formSections.length > 1)
  const sections = $derived(sectioned ? formSections : [{ key: 'all', form: '', jibo: null, items }])
  // Visual/DOM order of tiles, used by Shift-click range selection, keyboard picks and inspector stepping.
  const ordered = $derived(sections.flatMap(section => section.items))
  const indexById = $derived(new Map(ordered.map((item, i) => [item.id, i])))
  function setView(next) {
    const resolved = switchQuizView(next, { cardsSize, compactSize })
    view = resolved.view; tileSize = resolved.size
    remember('atlas.quiz.view', view)
    remember('atlas.quiz.tileSize', tileSize)
  }
  function setTileSize(next) {
    const resolved = resizeQuizTiles(next, view)
    view = resolved.view; tileSize = resolved.size
    if (view === 'compact') { compactSize = tileSize; remember('atlas.quiz.tileSize.compact', compactSize) }
    else { cardsSize = tileSize; remember('atlas.quiz.tileSize.cards', cardsSize) }
    remember('atlas.quiz.view', view)
    remember('atlas.quiz.tileSize', tileSize)
  }
  function toggleGroupByForm() {
    groupByForm = !groupByForm
    anchor = null
    remember('atlas.quiz.group-by-form', groupByForm)
  }
  function sectionStatus(section) {
    const open = section.items.filter(i => selectable(i.id))
    const picked = section.items.filter(i => selected[i.id])
    const pickedOpen = open.filter(i => selected[i.id])
    const state = !open.length ? (picked.length ? 'all' : 'none')
      : pickedOpen.length === open.length ? 'all' : pickedOpen.length ? 'some' : 'none'
    return { open, picked, state }
  }
  function toggleSection(section) {
    if (saving || loading) return
    const { open, picked, state } = sectionStatus(section)
    if (state === 'all') {
      const ids = picked.map(i => i.id)
      if (!ids.length) return
      choices = without(choices, ids)
      selected = without(selected, ids)
    } else if (open.length) {
      selected = { ...selected, ...Object.fromEntries(open.map(i => [i.id, true])) }
    }
    anchor = null; at = 0
  }
  const compactTitle = item => {
    const lines = cropDetails(item)
    return [item.label, ...lines].filter(Boolean).join(' · ')
  }
  // One tile per crop: the grid is keyed by id, and a repeated id would stop the round rendering.
  function unique(list) { const ids = new Set(); return list.filter(item => !ids.has(item.id) && ids.add(item.id)) }
  function numbered(list, from = 0, fallback = '') {
    return list.map((item, i) => ({ ...item, dealt: item.dealt ?? from + i, dealtForm: item.dealtForm ?? cropSectionForm(item, fallback) }))
  }
  function arranged(list) {
    return [...list].sort((a, b) => byShape
      ? (a.shape_order ?? Infinity) - (b.shape_order ?? Infinity) || a.dealt - b.dealt
      : a.dealt - b.dealt)
  }
  function toggleShape() { byShape = !byShape; anchor = null; remember('atlas.quiz.shape-order', byShape); items = arranged(items) }
  // A reload that kept its round deals it again; a page reached by Back after a reload carries the round its
  // entry named; otherwise the address names it. Each replaces the address with the round it deals.
  onMount(() => {
    last = stored('atlas.last-round', null)
    const savedView = stored('atlas.quiz.view', 'cards') === 'compact' ? 'compact' : 'cards'
    cardsSize = normalizeQuizTileSize(stored('atlas.quiz.tileSize.cards', QUIZ_CARDS_SIZE), QUIZ_CARDS_SIZE)
    if (cardsSize < QUIZ_CARDS_MIN) cardsSize = QUIZ_CARDS_SIZE
    compactSize = normalizeQuizTileSize(stored('atlas.quiz.tileSize.compact', QUIZ_COMPACT_SIZE), QUIZ_COMPACT_SIZE)
    const savedSize = normalizeQuizTileSize(stored('atlas.quiz.tileSize', null), savedView === 'compact' ? compactSize : cardsSize)
    const resolved = resizeQuizTiles(savedSize, savedView)
    view = resolved.view; tileSize = resolved.size
    groupByForm = stored('atlas.quiz.group-by-form', true) !== false
    const kept = carried(), round = page.state.round
    if (kept) resume(kept)
    else load(round ? { target: round.grapheme || null, scope: materialOf(round.production), how: 'replace', only: Boolean(round.suspects) }
      : { target: initialGrapheme || null, how: 'replace', only: locationRound().suspects })
    return () => { closed = true }
  })
</script>

<svelte:window onkeydown={keydown} />

{#snippet quizTile(item, i)}
  <div class="quiz-tile" use:watchSeen={item.id} data-unit={item.id} class:selected={selected[item.id]} class:wrong={choices[item.id]?.verdict === 'wrong' || recorded[item.id] === 'flagged'} class:assigned={recorded[item.id] === 'assigned'} class:unavailable={failed[item.id]} class:skipped={skipped[item.id]} class:recorded={recorded[item.id]}>
    <button class="quiz-choice" aria-label={t('quiz.selectCharacter', { number: i + 1 })} aria-pressed={!!selected[item.id]} title={compact ? compactTitle(item) : undefined} disabled={saving || !loaded[item.id] || decidedHere(item.id)} onclick={event => toggle(item.id, event)}><Glyph {item} eager onload={id => loaded = { ...loaded, [id]: true }} onerror={id => { failed = { ...failed, [id]: true }; if (selected[id]) skip([id]) }} /><span class="choice-mark">{selected[item.id] ? '✓' : choices[item.id]?.verdict === 'wrong' ? '×' : ''}</span></button>
    <span class="tile-details quiz-tile-details" aria-hidden="true">{#each cropDetails(item) as line}<span><ScriptLine {line} /></span>{/each}<CopyId id={item.id} inline /></span>
    {#if compact && (markedAs[item.id] || choices[item.id]?.character || (!sectioned && item.label !== graphemeChar) || recorded[item.id] || failed[item.id] || skipped[item.id])}
      <span class="compact-tag" aria-hidden="true">{#if markedAs[item.id]}<span class="quiz-written">→ <ScriptText text={markedAs[item.id]} /></span>{:else if choices[item.id]?.character}<span class="quiz-written" class:wrong-read={taken(item)}>→ <ScriptText text={choices[item.id].character} /></span>{:else if failed[item.id]}<span class="compact-status">{t('quiz.choiceLabel.unavailable')}</span>{:else if skipped[item.id]}<span class="compact-status">{t('quiz.choiceLabel.skipped')}</span>{:else if recorded[item.id]}<span class="recorded-badge" class:refused={keptHere[item.id] && keptState(keptHere[item.id]) === 'refused'}>{recordedLabel(item.id)}</span>{:else if !sectioned && item.label !== graphemeChar}<span class="quiz-written"><ScriptText text={item.label} /></span>{/if}</span>
    {/if}
    <div class="quiz-production">{#if markedAs[item.id]}<span class="quiz-written">→ <ScriptText text={markedAs[item.id]} /></span>{:else if item.label !== graphemeChar}<span class="quiz-written"><ScriptText text={item.label} /></span>{/if}<ProductionBadge {item} hideUnknown />{#if choices[item.id]?.character && !taken(item)}<span class="quiz-written">→ <ScriptText text={choices[item.id].character} /></span>{/if}{#if onlySuspects && item.suspect && !reading(item)}<span class="suspect-badge">{t('quiz.suspect.doubtful')}</span>{/if}{#if recorded[item.id]}<span class="recorded-badge" class:refused={keptHere[item.id] && keptState(keptHere[item.id]) === 'refused'}>{recordedLabel(item.id)}</span>{/if}{#if item.origin === 'corpus'}<span class="quiz-source" lang={item.source?.title ? 'ja' : undefined} title={item.source?.title}>{item.source?.title ?? t('quiz.corpusSource')}</span>{/if}</div><div class="quiz-tile-tools">{#if keys[i]}<kbd>{keys[i]}</kbd>{/if}{#if reading(item) && (onlySuspects || selected[item.id]) && !failed[item.id] && !skipped[item.id]}<button class="reading-choice" aria-pressed={taken(item)} aria-label={t('quiz.suspect.readsAs', { character: reading(item) }) + ' · ' + (i + 1)} disabled={saving || !selectable(item.id)} title={t('quiz.suspect.readsAs', { character: reading(item) })} onclick={event => takeReading(item, event.currentTarget)}>{#if selected[item.id] || compact}{taken(item) ? '→ ' : ''}<span lang="ja">{reading(item)}</span>{taken(item) ? '' : '?'}{:else}{@const [before, after] = around('quiz.suspect.readsAs', 'character')}{before}<span lang="ja">{reading(item)}</span>{after}{/if}</button>{/if}<span class="choice-label">{failed[item.id] ? t('quiz.choiceLabel.unavailable') : skipped[item.id] ? t('quiz.choiceLabel.skipped') : ''}</span>{#if selected[item.id] && !skipped[item.id] && !failed[item.id]}<button class="correct-choice" aria-label={t('quiz.correctCharacter', { number: i + 1 })} title={t('quiz.correct')} disabled={saving} onclick={() => correctOne(item)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20l1-4.5L16.5 4l3.5 3.5L8.5 19z" /></svg></button>{/if}<button class="inspect-choice" aria-label={t('quiz.inspectCharacter', { number: i + 1 })} disabled={saving} onclick={() => inspectChoice(item)}>↗</button>{#if skipped[item.id]}<button class="restore-choice" aria-label={t('quiz.restoreCharacter', { number: i + 1 })} disabled={saving} onclick={() => restore(item.id)}>{t('quiz.restore')}</button>{:else}<button class="skip-choice" aria-label={t('quiz.skipCharacter', { number: i + 1 })} title={skipHint()} disabled={saving} onclick={() => skip([item.id])}>–</button>{/if}</div>
  </div>
{/snippet}
<section class="quiz-workspace">
  <div class="quiz-topline"><a href={localize('/')} class="quiet-link">{t('quiz.backToCollection')}</a>{#if grapheme}<a href={localize(graphemeAddress(grapheme, graphemeChar))} class="quiet-link open-explore"><ScriptLine line={{ ...withText('quiz.openInExplore', 'grapheme', { grapheme: graphemeName }), ...labelParts(graphemeChar) }} /></a>{/if}<div class="round-count"><span class="live-dot"></span>{t('quiz.issuesSavedSession', { count: completed })}</div><button class="undo-round shape-toggle" aria-pressed={byShape} onclick={toggleShape}>{byShape ? t('quiz.shapeToggle.byShape') : t('quiz.shapeToggle.dealt')}</button><button class="undo-round suspect-toggle" aria-pressed={onlySuspects} disabled={saving || loading || selection.length > 0 || step !== 'select'} onclick={toggleSuspects}>{onlySuspects ? t('quiz.suspectToggle.only') : t('quiz.suspectToggle.all')}</button>{#if last}<button class="undo-round" disabled={saving} onclick={undo}>{t('quiz.undoLastRound')}</button>{/if}</div>
  <div class="quiz-heading"><div class="target-character" class:pair={labelSize(graphemeChar) === 2} class:trio={labelSize(graphemeChar) === 3} aria-label={t('quiz.targetGrapheme', { grapheme: graphemeName })}>{#if graphemeChar}<ScriptText {...labelParts(graphemeChar)} />{:else}<span lang="ja">字</span>{/if}</div><div class="quiz-title"><div class="quiz-heading-row"><h1>{step === 'select' ? t('quiz.heading.select') : t('quiz.heading.issue')}</h1>{#if step === 'select'}<QuizGuide />{/if}</div>{#if step === 'select'}<p>{around('quiz.selectHint', 'grapheme')[0]}<b>{#if graphemeChar}<ScriptText {...labelParts(graphemeChar)} />{:else}…{/if}</b>{around('quiz.selectHint', 'grapheme')[1]}</p>{/if}{#if members.length > 1}<p class="round-members">{#each members as member, i (i)}{#if i}{' '}{/if}<ScriptText text={member} />{/each}</p>{/if}</div><div class="round-switch"><button class="category-toggle" disabled={saving || loading} onclick={() => categoryOpen = !categoryOpen}>{t('quiz.changeCharacter')}</button><button class="quiet-link" disabled={saving || loading || (!canNext && !recordable)} onclick={pass}>{t('quiz.nextCharacterArrow')}</button></div></div>
  {#if categoryOpen}<div class="round-categories"><input aria-label={t('quiz.findCategory.aria')} placeholder={t('quiz.findCategory.placeholder')} bind:value={search}/><div class="category-options">{#each categories as c (c.label)}<button disabled={saving} onclick={() => chooseCategory(c.label)}><span class:pair={labelSize(c.char) === 2} class:trio={labelSize(c.char) === 3}><ScriptText {...labelParts(c.char)} /></span>{#if c.members.length > 1}<small class="category-members"><ScriptText text={c.members.map(m => m.label).join('')} /></small>{/if}<small>{c.pending}</small></button>{/each}</div></div>{/if}
  <label class="review-material">{t('quiz.material.label')}
    <select aria-label={t('quiz.material.aria')} value={production} disabled={saving || loading || loadingMore}
      onchange={event => load({ scope: event.currentTarget.value, target: grapheme })}>
      {#each MATERIALS as [value, label]}<option {value}>{label()}</option>{/each}
    </select>
  </label>
  {#if history.length > 1}
    <nav class="character-history" aria-label={t('quiz.history.label')}>
      <button class="history-previous" aria-label={t('quiz.history.previousRound')} disabled={saving || loading || historyIndex <= 0} onclick={() => visit(historyIndex - 1)}>{t('quiz.history.previous')}</button>
      <div class="history-characters">{#each history as round, i (round.roundId)}<button class="history-character" class:current={i === historyIndex} aria-current={i === historyIndex ? 'step' : undefined} aria-label={t('quiz.history.returnTo', { grapheme: graphemeLabel(graphemeText(round.grapheme)) })} disabled={saving || loading} onclick={() => visit(i)}><ScriptText {...labelParts(graphemeText(round.grapheme))} /></button>{/each}</div>
      <button class="history-next" aria-label={t('quiz.history.nextRound')} disabled={saving || loading || historyIndex >= history.length - 1} onclick={() => visit(historyIndex + 1)}>→</button>
    </nav>
  {/if}
  {#if updated.current}<div class="update-notice" role="status"><span>{t('quiz.newVersion')}</span><button disabled={saving} onclick={reloadKeepingRound}>{t('quiz.reloadNewVersion')}</button></div>{/if}
  {#if error}<div class="error-message" role="alert"><span>{error}</span>{#if stale}<button disabled={saving} onclick={() => load({ target: grapheme, replace: true })}>{t('quiz.reloadRound')}</button>{/if}</div>{/if}

  {#if step === 'select'}
    <div class="stage-toolbar">
      <strong>{openSelection.length ? t('quiz.select.selectedCount', { count: openSelection.length }) : t('quiz.select.selectProblems')}</strong>
      <button class="bulk-toggle" disabled={saving || loading || !available} onclick={selectAll}>{decidable.length && decidable.every(i => selected[i.id]) ? t('quiz.bulk.none') : t('quiz.bulk.all')}</button>
      <button class="bulk-toggle skip-selection" disabled={saving || loading || !selection.length} onclick={() => skip()} title={skipHint()}>{t('quiz.skipSelected', { skip: skipLabel() })}</button>
      <span class="keyboard-hint">{t('quiz.keyboardHint.pickCrop')}</span>
      <div class="quiz-view-controls">
        <ImageStyleToggle ink={session.state.ink} onchange={value => session.setInk(value)} />
        <button type="button" class="form-group-toggle" aria-pressed={groupByForm} onclick={toggleGroupByForm}>{t('quiz.groupByForm')}</button>
        <div class="layout-toggle quiz-mode-toggle" role="group" aria-label={t('quiz.view.label')}>
          {#each [['cards', () => t('quiz.view.cards')], ['compact', () => t('quiz.view.compact')]] as [value, label]}
            <button type="button" class:active={view === value} aria-pressed={view === value} onclick={() => setView(value)}>{label()}</button>
          {/each}
        </div>
        <TileSizeSlider class="quiz-zoom" min={0} max={QUIZ_TILE_SIZES.length - 1} value={sizeIndex} label={t('explore.zoom.label')} valuetext={`${tileSize}px`} oninput={i => setTileSize(QUIZ_TILE_SIZES[i])} />
      </div>
    </div>
    {#key roundId}<div class="quiz-grid" class:compact class:tight={compact && tileSize <= 60} class:sectioned={!loading && sectioned} style:--quiz-tile-w={`${tileSize}px`} style:--quiz-gap={`${gridGap}px`} aria-label={t('quiz.grid.label')} aria-busy={loading}>
      {#if loading}{#each Array(compact ? 24 : 12) as _}<div class="quiz-skeleton"></div>{/each}
      {:else if sectioned}
        {#each sections as section (section.key)}
          {@const status = sectionStatus(section)}
          <section class="quiz-section" data-form={section.form}>
            <header class="quiz-section-head">
              <button type="button" class="section-select" role="checkbox"
                aria-checked={status.state === 'some' ? 'mixed' : status.state === 'all'}
                aria-disabled={!status.open.length && status.state === 'none'}
                aria-label={t('quiz.section.select', { form: section.form || graphemeName })}
                title={t('quiz.section.select', { form: section.form || graphemeName })}
                disabled={saving || loading}
                onclick={() => toggleSection(section)}>
                <span aria-hidden="true">{status.state === 'all' ? '✓' : status.state === 'some' ? '–' : ''}</span>
              </button>
              <h2 class="quiz-section-form">{#if section.form}<FormText text={section.form} />{:else}<ScriptText {...labelParts(graphemeChar)} />{/if}</h2>
              {#if section.jibo}<span class="quiz-section-jibo"><ScriptText text={section.jibo} /></span>{/if}
              <span class="quiz-section-count">{#if status.picked.length}{number(status.picked.length)} / {/if}{number(section.items.length)}</span>
            </header>
            <div class="quiz-section-grid">
              {#each section.items as item (item.id)}
                {@render quizTile(item, indexById.get(item.id) ?? 0)}
              {/each}
            </div>
          </section>
        {/each}
        {#if loadingMore}<div class="quiz-section-grid">{#each Array(compact ? 12 : 6) as _}<div class="quiz-skeleton" aria-hidden="true"></div>{/each}</div>{/if}
      {:else}
        {#each ordered as item, i (item.id)}
          {@render quizTile(item, i)}
        {/each}
        {#if loadingMore}{#each Array(compact ? 12 : 6) as _}<div class="quiz-skeleton" aria-hidden="true"></div>{/each}{/if}
      {/if}
    </div>
    {/key}
    <!-- More crops load on their own as the reader nears the end; the row only says what is happening.
         A button remains for the case scrolling cannot trigger (a failed batch waits for a retry). -->
    {#if items.length}<div class="load-more-row" use:watchEnd role="status">
      {#if loadingMore}<span class="load-more-status"><span class="load-more-spinner" aria-hidden="true"></span>{t('quiz.loadMore.loading')}</span>
      {:else if hasMore && items.length >= roundLimit}<span class="load-more-status">{t('quiz.loadMore.saveToLoad')}</span>
      {:else if hasMore}<button class="load-more" disabled={loading || saving} onclick={loadMore}>{loadMoreFailed && error ? t('common.retry') : t('quiz.loadMore.more', { grapheme: graphemeName })}</button>
      {:else}<span class="load-more-status">{t('quiz.loadMore.allLoaded', { grapheme: graphemeName })}</span>{/if}
    </div>{/if}
    {#if references.length}
      <section class="quiz-reference" aria-label={t('quiz.reference.label')}>
        <p class="reference-heading">{t('quiz.reference.heading')} <span>{number(references.length)}</span></p>
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
    <QuizFocus items={queue} {skipped} index={focusIndex} label={t('quiz.focus.chooseProblem')} disabled={saving} onback={back} onjump={jump} onprev={() => move(-1)} onnext={() => move(1)}>
      <!-- One panel per crop, so a search begun for one crop never shows in the next one's. -->
      {#snippet formBar()}<CropForm crop={current} others onchoose={markCurrent} disabled={saving || loading} />
        {#if formDone}<p class="mark-done" role="status">{formDone.kind === 'style' ? t('bulk.styled', { count: formDone.count, style: t(`style.kind.${formDone.style}`) }) : formDone.kind === 'confirm' ? t('bulk.confirmed', { count: formDone.count }) : t('bulk.done', { count: formDone.count, char: formDone.char })}<button class="quiet-link" disabled={saving} onclick={undoForm}>{t('bulk.undo')}</button></p>{/if}{#if formError}<p class="mark-done mark-error" role="alert">{formError}</p>{/if}{/snippet}
      {#key current.id}<CropReview forms={formBar} issues={currentIssues} onissue={assignCurrent} disabled={saving}
        onskip={() => skip([current.id])} skipped={!!skipped[current.id]}
        targetId={current.id} bind:element={suggestionsElement} result={suggestions[current.id]} loading={!suggestions[current.id]} contextResult={contextSuggestions[current.id] ?? null} contextLoading={!contextSuggestions[current.id]} label={current.label} value={choices[current.id]?.character || choices[current.id]?.correction} noneSelected={choices[current.id]?.noneSelected ?? false} onchoose={(value, none) => chooseSuggestion(current.id, value, none)} />{/key}
      <!-- The box is redrawn in the inspector, which saves the crop with it. -->
      {#if currentIssues.includes('crop') && current.crop_editable !== false}<div class="crop-change"><button type="button" class="quiet-link adjust-crop" disabled={saving} onclick={() => inspectChoice(current, { issues: currentIssues, character: choices[current.id].character, correction: choices[current.id].correction })}>{t('character.crop.adjust')}</button></div>{/if}
    </QuizFocus>
  {/if}

  {#if step === 'select' && !loading && !items.length}<div class="empty"><span class="empty-mark">字</span><h2>{categories.length ? t('quiz.empty.chooseCharacter') : t('quiz.empty.allCaughtUp')}</h2>{#if categories.length}<button class="primary" onclick={() => categoryOpen = true}>{t('quiz.chooseCharacterButton')}</button>{:else}<a href={localize('/flagged')} class="primary">{t('quiz.reviewFlagged')}</a>{/if}</div>
  {:else}<div class="quiz-actionbar">{#if step === 'select' && (markable.length || formDone || formError)}<FormBar count={markable.length} forms={members} grapheme={graphemeChar} busy={saving} error={formError} done={formDone} bind:style={formStyle}
    onconfirm={confirmSelection} onassign={markSelection} onstyle={styleSelection} onclear={clearSelection} onundo={undoForm} ondismiss={() => { formDone = null; formError = '' }} />{/if}<div class="round-selection"><span class="selection-dot" class:has-flags={decided > 0}></span><strong>{t('quiz.decided', { count: decided })}</strong>{#if undecided}<span>{t('quiz.undecided', { count: undecided })}</span>{/if}{#if Object.keys(skipped).length}<small>{t('quiz.skippedNotSaved', { count: Object.keys(skipped).length })}</small>{/if}{#if Object.keys(failed).length}<small>{t('quiz.unavailableCount', { count: Object.keys(failed).length })}</small>{/if}</div><div class="quiz-submit">
    <span class="keyboard-hint">{step === 'select' ? t('quiz.keyboardHint.select') : t('quiz.keyboardHint.forms') + ' · ' + (offersSuggestions(currentIssue) ? t('quiz.keyboardHint.correct') + ' · ' + t('quiz.keyboardHint.issues') : t('quiz.keyboardHint.issues'))}</span>
    {#if step === 'select'}<button class="quiet-link skip-selected" disabled={loading || saving || exhausted || (!decidable.length && !selection.length)} onclick={() => skip(selection.length ? selection : decidable.map(i => i.id))} title={skipHint()}>{selection.length ? t('quiz.skipSelected', { skip: skipLabel() }) : skipLabel()}</button>{/if}
    {#if exhausted || !openSelection.length}<button class="primary next-round" disabled={loading || saving || (!canNext && !recordable)} onclick={pass}>{t('quiz.nextCharacterLabel')} <span>→</span></button>
    {:else if step === 'select' && answered}<button class="quiet-link review-selected" disabled={loading || saving || loadingMore || !ready} onclick={reviewSelected}>{t('quiz.reviewSelected', { count: selection.length })}</button><button class="primary save-round" disabled={loading || saving || loadingMore || !ready} onclick={submit}>{saving ? t('common.saving') : t('quiz.saveIssues')} <span>✓</span></button>
    {:else if step === 'select'}<button class="primary review-selected" disabled={loading || saving || loadingMore || !ready} onclick={reviewSelected}>{t('quiz.reviewSelected', { count: selection.length })} <span>→</span></button>
    {:else if focusIndex < queue.length - 1}<button class="primary next-crop" disabled={loading || saving || !(choices[current?.id] || skipped[current?.id])} onclick={primary}>{t('quiz.nextCrop')} <span>→</span></button>
    {:else if !answered}<button class="primary finish-issues" disabled={loading || saving || !(choices[current?.id] || skipped[current?.id])} onclick={primary}>{t('quiz.reviewRemaining')} <span>→</span></button>
    {:else}<button class="primary save-round" disabled={loading || saving || loadingMore || !ready} onclick={submit}>{saving ? t('common.saving') : t('quiz.saveIssues')} <span>✓</span></button>{/if}
  </div><ContributionTerms /></div>{/if}
</section>

{#if savedNotice}<div class="save-toast quiz-saved" role="status">✓ {savedNotice}</div>{/if}
<style>
  .update-notice{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:13px 16px;margin-bottom:20px;border-radius:7px;background:var(--accent-light);color:var(--accent);font-size:13px}
  .update-notice button{white-space:nowrap;background:transparent;color:inherit;border-color:currentColor;font-size:12px}
  .quiz-heading-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .review-material{display:flex;align-items:center;gap:10px;margin:0 0 20px;font-size:12px;color:var(--muted)}
  /* Branded native select (`appearance: base-select`) with graceful fallback to native dropdown. */
  .review-material select,
  .review-material select::picker(select){appearance:base-select}
  .review-material select{min-width:13em;max-width:100%;padding:7px 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);font:inherit;cursor:pointer;align-items:center;gap:10px}
  .review-material select:hover:not(:disabled){border-color:var(--line-strong)}
  .review-material select:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  .review-material select::picker-icon{color:var(--muted);transition:transform .15s}
  .review-material select:open::picker-icon{transform:rotate(180deg)}
  .review-material select::picker(select){margin-block-start:4px;padding:6px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--ink);box-shadow:0 12px 30px var(--shadow-menu);font:inherit}
  .review-material select option{padding:7px 10px;border-radius:5px;color:var(--ink);cursor:pointer;gap:8px}
  .review-material select option:hover,
  .review-material select option:focus-visible{background:var(--accent-hover);color:var(--accent)}
  .review-material select option:checked{background:var(--accent-light);color:var(--accent);font-weight:500}
  .review-material select option::checkmark{color:var(--accent)}
  /* Scoped to this view on purpose: the skipped state is the round's own, and the tile keeps the
     size and position it had so declining a crop does not reflow the grid under the reader. */
  .quiz-production{display:flex;gap:6px;min-width:0;padding:0 12px 4px}
  .quiz-production:empty{display:none}
  /* A round deals every character of its grapheme; a tile names its own when it is not the head. */
  .quiz-written{flex:none;font-size:13px;line-height:1.2;color:var(--accent)}
  .round-members{margin:4px 0 0;font-size:15px;letter-spacing:.08em;color:var(--muted)}
  .category-members{max-width:7em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .category-members :global(span){font-size:inherit}
  /* A tile's details sit above its tools, so the keys and buttons stay visible and clickable. */
  .quiz-tile-details{bottom:32px;padding-bottom:10px}
  /* Keyboard focus shows it too; a click that leaves focus on the tile does not keep it open. */
  .quiz-tile:hover .quiz-tile-details,.quiz-tile:has(:focus-visible) .quiz-tile-details{opacity:1;transform:none}
  /* The id takes clicks only while its card shows, and only for a pointer that hovers: a touch screen
     keeps a tapped tile in :hover, and the next tap there must still toggle the crop. */
  @media (hover:hover){.quiz-tile:hover .quiz-tile-details :global(.copy-inline){pointer-events:auto}}
  .quiz-source{flex:1;min-width:0;overflow:hidden;font-size:10px;line-height:1.5;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}
  .quiz-tile.skipped { background: var(--surface-skipped); border-style: dashed; }
  .quiz-tile.skipped .quiz-choice { opacity: .35; }
  .restore-choice { font-size: 9px; padding: 1px 7px; }
  /* One stage at a time: the selection toolbar belongs to step one and is not shown again. */
  .stage-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0 12px; font-size: 12px; }
  .stage-toolbar strong { font-weight: 500; }
  .quiz-view-controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-inline-start: auto; }
  :global(html:not([data-hydrated])) .quiz-view-controls { visibility: hidden; }
  .form-group-toggle { border: 1px solid var(--line); border-radius: 6px; background: var(--surface); font-size: 11px; padding: 6px 10px; color: var(--muted); white-space: nowrap; }
  .form-group-toggle[aria-pressed="true"] { background: var(--surface-selected); border-color: var(--line-strong); color: var(--ink); }

  /* Grid sizing driven by --quiz-tile-w with auto-fill so small form sections keep uniform tile width. */
  .quiz-grid:not(.sectioned),
  .quiz-section-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(var(--quiz-tile-w, 160px), 1fr));
    gap: var(--quiz-gap, 12px);
    align-items: start;
  }
  .quiz-grid.sectioned { display: flex; flex-direction: column; align-items: stretch; gap: 22px; }
  .quiz-section { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .quiz-section-head { display: flex; align-items: center; gap: 10px; padding-block-end: 8px; border-block-end: 1px solid var(--line); }
  .quiz-section-form { margin: 0; font-size: 24px; font-weight: 500; line-height: 1.15; font-family: var(--char-font, serif); }
  .quiz-section-jibo { font-size: 13px; color: var(--muted); }
  .quiz-section-count { margin-inline-start: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .section-select { position: relative; flex: none; display: flex; align-items: center; justify-content: center; width: 18px; height: 18px; border: 2px solid var(--muted); border-radius: 5px; background: var(--surface); color: var(--on-color); font: 700 11px/1 var(--ga-font-ui, system-ui, sans-serif); padding: 0; }
  .section-select::after { content: ''; position: absolute; inset: -10px -8px; }
  .section-select:hover:not(:disabled):not([aria-disabled="true"]) { border-color: var(--accent-solid); }
  .section-select[aria-disabled="true"] { border-color: var(--line); cursor: default; }
  .section-select:not([aria-checked="false"]) { background: var(--accent-solid); border-color: var(--accent-solid); }

  /* Card tiles scale their image height smoothly with --quiz-tile-w. */
  .quiz-grid:not(.compact) .quiz-tile { height: auto; min-height: 0; }
  .quiz-grid:not(.compact) .quiz-choice { height: clamp(104px, calc(var(--quiz-tile-w, 160px) * 0.88), 184px); }
  .quiz-grid:not(.compact) .quiz-skeleton { height: clamp(140px, calc(var(--quiz-tile-w, 160px) * 1.08), 216px); min-height: 0; }

  /* Compact thumbnail mode: dense square crops for fast visual scanning. */
  .quiz-grid.compact .quiz-tile { aspect-ratio: 1; height: auto; min-height: 0; border-radius: 6px; }
  .quiz-grid.compact .quiz-skeleton { aspect-ratio: 1; height: auto; min-height: 0; border-radius: 6px; }
  .quiz-grid.compact .quiz-choice { width: 100%; height: 100%; padding: 5px; }
  .quiz-grid.compact.tight .quiz-choice { padding: 3px; }
  .quiz-grid.compact .choice-mark { top: 3px; right: 3px; font-size: 16px; }
  .quiz-grid.compact .quiz-tile.selected .choice-mark { width: 17px; height: 17px; font-size: 10px; top: 3px; right: 3px; }
  .quiz-grid.compact .quiz-tile-details,
  .quiz-grid.compact .quiz-production { display: none; }
  .compact-tag { position: absolute; top: 3px; left: 3px; z-index: 1; max-width: calc(100% - 26px); padding: 1px 4px; border-radius: 4px; background: var(--overlay); font-size: 10px; line-height: 1.25; pointer-events: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .compact-tag .quiz-written { font-size: 10px; }
  .compact-tag .wrong-read { color: var(--wrong); }
  .compact-status { font-size: 9px; color: var(--muted); }
  .quiz-grid.compact .quiz-tile-tools { position: absolute; left: 3px; right: 3px; bottom: 3px; z-index: 2; height: auto; min-height: 0; padding: 0; gap: 2px; justify-content: flex-end; opacity: 0; pointer-events: none; transition: opacity .12s; }
  .quiz-grid.compact .quiz-tile:hover .quiz-tile-tools,
  .quiz-grid.compact .quiz-tile:has(:focus-visible) .quiz-tile-tools { opacity: 1; pointer-events: auto; }
  .quiz-grid.compact .quiz-tile-tools kbd,
  .quiz-grid.compact .quiz-tile-tools .choice-label { display: none; }
  .quiz-grid.compact .quiz-tile-tools button { min-width: 20px; min-height: 20px; padding: 0 4px; font-size: 11px; line-height: 1; background: var(--overlay); border: 1px solid var(--line); border-radius: 4px; color: var(--ink); box-shadow: 0 1px 4px var(--shadow-soft); }
  .quiz-grid.compact .quiz-tile-tools .reading-choice { font-size: 10px; padding: 1px 5px; border-color: var(--wrong); color: var(--wrong); background: var(--overlay); }
  @media (hover: none) {
    .quiz-grid.compact .quiz-tile-tools { display: none; }
    .quiz-grid.compact .quiz-tile:is(.selected, .recorded, .skipped, .unavailable) .quiz-tile-tools { display: flex; opacity: 1; pointer-events: auto; }
  }
  @media (max-width: 700px) {
    .quiz-view-controls { width: 100%; margin-inline-start: 0; justify-content: space-between; gap: 8px; }
    .quiz-grid:not(.compact):not(.sectioned),
    .quiz-grid:not(.compact) .quiz-section-grid {
      grid-template-columns: repeat(auto-fill, minmax(calc(var(--quiz-tile-w, 160px) * 0.68), 1fr));
      gap: 8px;
    }
    .quiz-grid:not(.compact) .quiz-choice { height: clamp(88px, calc(var(--quiz-tile-w, 160px) * 0.68), 140px); padding: 10px 10px 0; }
    .quiz-section-form { font-size: 21px; }
  }
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
  .reference-tile { display:flex; flex-direction:column; align-items:center; gap:4px; width:68px; padding:6px 4px; background:var(--surface-sunken); border:1px solid transparent; border-radius:7px; }
  .reference-tile:hover { border-color:var(--line-hover); }
  .reference-glyph { display:block; width:52px; height:52px; }
  .reference-tag { display:flex; align-items:center; gap:4px; font-size:8px; color:var(--muted); white-space:nowrap; }
  .status-dot.seen { background:light-dark(#8d8d95, #9d9da6); }
  /* Above the sticky save bar, which would otherwise sit behind it. */
  .save-toast.quiz-saved { bottom:108px; }
  /* A crop this round already saved: still shown, marked, and never sent again. */
  .recorded-badge { font-size:9px; color:var(--muted); white-space:nowrap; }
  .recorded-badge.refused { color:var(--wrong); }
  .suspect-badge { font-size:9px; color:var(--wrong); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  /* The reading is pressed to take it, among the tile's tools: the details that open over a tile leave those clear. */
  .quiz-tile-tools .reading-choice { flex:none; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:1px 8px; border:1px solid var(--wrong); border-radius:999px; color:var(--wrong); font-size:11px; line-height:1.2; }
  .reading-choice + .choice-label { min-width:0; }
  .quiz-tile-tools .reading-choice:hover:not(:disabled) { background:var(--wrong-light); color:var(--wrong); }
  .quiz-tile-tools .reading-choice[aria-pressed="true"] { background:var(--wrong-light); font-weight:600; }
  .quiz-tile-tools .correct-choice { display:inline-flex; align-items:center; justify-content:center; padding:0; }
  .correct-choice svg { width:13px; height:13px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linejoin:round; }
  /* A narrow tile keeps the reading on a line of its own, chosen or not. */
  @media(max-width:740px) { .quiz-tile-tools:has(.reading-choice) { flex-wrap:wrap; height:auto; } .quiz-tile-tools .reading-choice { order:-1; flex:1 0 100%; } }
  .suspect-toggle[aria-pressed="true"] { color:var(--wrong); }
  .quiz-tile.recorded .quiz-choice { opacity:.72; }
  .quiz-tile.assigned { border-color:var(--good); }
  /* The form bar sits over the actions, on a row of its own. */
  .quiz-actionbar { flex-wrap:wrap; }
  .mark-done { display:flex; gap:10px; align-items:center; margin:-6px 0 12px; font-size:12px; color:var(--muted); }
  .mark-error { color:var(--wrong); }
</style>
