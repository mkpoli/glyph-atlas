<script>
  import ContributionTerms from './ContributionTerms.svelte'
  import ProductionBadge, { productionLabel } from './ProductionBadge.svelte'
  import SourceCredit from './SourceCredit.svelte'
  import StyleField from './StyleField.svelte'
  import CropForm from './CropForm.svelte'
  import CropTitle from './CropTitle.svelte'
  import { setForm } from '../lib/cropForms.js'
  import ZiLink from './ZiLink.svelte'
  import CopyId from './CopyId.svelte'
  import Cite from './Cite.svelte'
  import CitedCut from './CitedCut.svelte'
  import { cropEntry } from '$worker/citation.ts'
  import FavouriteButton from './FavouriteButton.svelte'
  import { onMount, untrack, tick } from 'svelte'
  import { character, request, suggestionsFor } from '../lib/client.js'
  import { readCrop } from '../lib/cropCache.js'
  import { decision, isSingle, offersSuggestions, greetSuggestions } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  import { cropAddress, useInspector } from '../lib/inspector.svelte.js'
  import CropContext from './CropContext.svelte'
  import { repairOf } from '../lib/cropDetails.js'
  import SimilarCrops from './SimilarCrops.svelte'
  import CropReview from './CropReview.svelte'
  import LineStrip from './LineStrip.svelte'
  import { fromEdit, toSource as pageToSource } from '../lib/cropBox.js'
  import AdvanceSwitch from './AdvanceSwitch.svelte'
  import { useSession } from '../lib/session.svelte.js'
  // `onskip` is supplied by the caller that owns the queue. The dialog never decides what "next"
  // means: it reports that the reader declined to judge this occurrence, and the caller advances,
  // closes, or does something else. Without the prop a skip goes where a save would: the next
  // occurrence when the reader goes through the list in a row and there is one, otherwise close.
  // `initial` is the record the server rendered the page with, so the first load needs no request.
  // `preview` is the list's own row for the crop, drawn while the record loads. `cited` is the cut the
  // page's address cites (`citedCut`), noted over the crop when it is not the one shown.
  let { id, close, saved, changed = null, onVerdict = null, onskip = null,
        previous = null, next = null, position = '', initial = null, preview = null, cited = null } = $props()
  const first = untrack(() => initial)
  const session = useSession(), inspector = useInspector()
  // Going on to the next crop disables the focused save button while it loads, which drops its focus;
  // once the crop is ready, focus returns to the button the reader was pressing. The save names the
  // crop it leaves, and the load of another crop arms the return.
  let saveButton = $state(null), refocus = $state(false), leaving = null
  // The button is enabled on the render after the crop is ready, so focus waits for it.
  $effect(() => { if (refocus && loaded && fresh && !busy && saveButton) { refocus = false; tick().then(() => saveButton?.focus({ preventScroll: true })) } })
  // A round's crop returns to its round, and a crop opened on its own has nowhere to go on to.
  const advancing = $derived(!onVerdict && !onskip && session.state.advance && Boolean(next))
  let dialog, data = $state(first), error = $state(''), busy = $state(false)
  let issue = $state(null), noneSelected = $state(false), correction = $state(null)
  // The form the reviewer chose for the crop, held until the save; null keeps the one it has.
  let form = $state(null)
  // The character a reviewer chose for a wrong-character crop, and whether one was chosen: an
  // untouched crop writes no character.
  let written = $state(first?.label ?? ''), writtenDirty = $state(false)
  let editingBox = $state(false), box = $state(null)
  let suggestions = $state(null), suggesting = $state(false), loaded = $state(false), imageFailed = $state(false)
  // The decision this page already saved for the crop, restored when the reader steps back to it. While
  // the choices on screen are still that decision, the save button shows it chosen and only moves on.
  let kept = $state(null)
  const choicesNow = () => ({ issue, correction: issue ? correction : null, noneSelected, written: writtenDirty ? written : null })
  const settled = $derived(Boolean(kept) && box == null && form == null && JSON.stringify(choicesNow()) === JSON.stringify(kept))
  let contextSuggestions = $state(null), contextSuggesting = $state(false)
  let closed = false, generation = 0, submission = null, figure = $state(null), suggestionsElement = $state(null)
  // The character and the strokes around it come from one source image at one revision, so the
  // outline is the crop's own rectangle as a fraction of the context rectangle, both reported by
  // the server from the bounds it cut. A client-side adjustment replaces the rectangle.
  // An edit is held in page pixels, because that is what the API stores and validates; the context
  // view measures source pixels, because that is what the crop was cut from. The two are the same
  // rectangle only when the cached image is the page's own size, so the scale converts between them.
  const toSource = b => pageToSource(data, b)
  // A box saved on the site waits for the next publication to cut it; until then it is drawn the same way.
  const drawn = $derived(box ?? (data?.box_pending ? data.box : null))
  const boxStyle = $derived(data?.context_box ? (() => {
    const c = data.context_box, b = drawn ? toSource(drawn) : data.crop_box
    if (!b) return ''
    return `left:${100 * (b.x - c.x) / c.w}%;top:${100 * (b.y - c.y) / c.h}%;width:${100 * b.w / c.w}%;height:${100 * b.h / c.h}%`
  })() : '')
  // NDL reads lines, so a confident reading longer than one character hints at a merged crop.
  // Results stored before votes were recorded carry NDL's reading only among the candidates.
  const lineReading = $derived([...(suggestions?.votes || []), ...(suggestions?.candidates || [])].find(vote => vote.engine === 'NDLkotenOCR'))
  const suggestedIssue = $derived(lineReading && lineReading.score >= .65 && !isSingle(lineReading.text) ? 'merged' : null)
  let replaced = $state(false)
  async function load(target, redirected = false, preloaded = null) {
    const current = ++generation
    replaced = redirected
    dialog?.scrollTo({ top: 0 })
    // The list's row stands in until the record arrives; nothing can be saved from it.
    data = preloaded ?? (preview?.id === target ? preview : null); fresh = Boolean(preloaded); asked = false
    error = ''; form = null; issue = null; correction = null; noneSelected = false; box = null; editingBox = false
    written = ''; writtenDirty = false; kept = null
    contextSuggestions = null; contextSuggesting = false
    loaded = false; imageFailed = false; suggestions = null; suggesting = false; submission = null
    if (leaving && target !== leaving) { refocus = true; leaving = null }
    try {
      const result = preloaded ?? await readCrop(target)
      if (closed || current !== generation) return
      // A record the dialog already moved past (a style saved meanwhile) is not put back.
      if (data?.id === result.id && data.revision > result.revision) { fresh = true; return }
      if (result.image !== data?.image) loaded = false
      data = result; fresh = true
      kept = onVerdict ? null : standing(result)
      if (kept) { issue = kept.issue; correction = kept.correction; noneSelected = kept.noneSelected }
      written = kept?.written ?? result.label ?? ''; writtenDirty = kept?.written != null
    } catch (e) {
      if (closed || current !== generation) return
      // A link to a retired crop opens the crop that replaced it, and the address follows. A round's
      // tile does not: its verdict belongs to the crop it was dealt, so the round reports the error.
      if (e.replacedBy && !onVerdict && !redirected) {
        if (location.pathname === cropAddress(target)) inspector.replaced(e.replacedBy)
        return load(e.replacedBy, true)
      }
      replaced = false
      error = e.message
    }
  }
  // The suggestions are asked for once the crop is on screen and can be judged, so they never hold up
  // the crop's own images.
  let fresh = $state(Boolean(first)), asked = false
  $effect(() => {
    if (!loaded || !fresh || !data || asked) return
    asked = true
    const result = data, current = generation
    contextSuggesting = true
    suggestionsFor(result, 'context').then(value => { if (!closed && current === generation) { contextSuggestions = value; contextSuggesting = false } })
    suggesting = true
    suggestionsFor(result).then(value => { if (!closed && current === generation) { suggestions = value; suggesting = false } })
  })
  // Only the first load, of the crop the page was rendered for, starts from `initial`.
  let preloaded = first
  // The entry of a replaced crop is rewritten to the crop on screen, which is already loaded.
  $effect(() => { const target = id; untrack(() => { if (replaced && target === data?.id) return; load(target, false, preloaded); preloaded = null }) })
  // The server renders the dialog open, so the page reads whole before any script runs; once it does,
  // the dialog is reopened as a modal.
  onMount(() => { if (dialog.open) dialog.close(); dialog.showModal(); return () => { closed = true; generation++ } })
  // ← and → step through the list, as the arrows in the header do; the crop view keeps its own arrows.
  function stepKey(event) {
    if (event.defaultPrevented || busy || event.metaKey || event.ctrlKey || event.altKey) return
    if (event.target.closest?.('input, textarea, select, .crop-viewport, .character-search')) return
    if ([...document.querySelectorAll('dialog[open]')].at(-1) !== dialog) return
    if (event.key === 'ArrowLeft' && previous) { event.preventDefault(); previous() }
    else if (event.key === 'ArrowRight' && next) { event.preventDefault(); next() }
  }
  function chooseIssue(value) {
    if (issue === 'character') { written = data?.label ?? ''; writtenDirty = false }
    issue = value; correction = null; noneSelected = false; submission = null
    // A crop is redrawn only for a bad crop; choosing another problem drops the new box.
    if (value !== 'crop') { box = null; editingBox = false }
    // A bad crop is redrawn there and then, where the crop can be adjusted.
    else if (!onVerdict && data?.context && data.context_box && data.crop_editable !== false) beginCrop()
    // The suggestion area appears with this choice, so the next action is the one focused. An issue
    // with no suggestions moves nothing, and no later arrival takes the focus back.
    if (!offersSuggestions(value)) return
    queueMicrotask(() => greetSuggestions(suggestionsElement, { focus: true }))
  }
  /** A suggestion that is one character names the character, so it corrects the written identity. */
  function chooseSuggestion(value, none = false) {
    noneSelected = none
    submission = null
    if (!value && issue === 'character') {
      written = data?.label ?? ''; writtenDirty = false
    }
    if (value && isSingle(value) && issue !== 'merged') {
      // One character names the character: the chosen value is carried as the written identity, and
      // the suggestion list highlights it from `written` rather than from `correction`. A round
      // takes typed characters only for a joined crop, so nothing goes into `correction`.
      written = value; writtenDirty = true; correction = null; noneSelected = false; issue = 'character'
      return
    }
    // Typed characters belong to a joined crop; on any other issue they name nothing, and a character
    // half-typed before them is not kept either.
    if (value && issue !== 'merged') { written = data?.label ?? ''; writtenDirty = false; correction = null; return }
    correction = value
  }

  /**
   * The decision this page saved for a crop, while the crop still holds it. The first read after the save
   * pins the revision it shows, and any later write (a round, a bulk correction, an undo) moves the crop
   * past it. A write between the save and that read is caught by the crop's state.
   */
  function standing(record) {
    const decided = inspector.decided(record.id)
    if (!decided) return null
    const { choices } = decided
    if (decided.revision == null) {
      // A character written into the crop shows as its label; any other decision as its state.
      const holds = choices.written != null ? choices.written === record.label : record.state === (choices.issue ? 'flagged' : 'checked')
      if (!holds) return null
      inspector.remember(record.id, { choices, revision: record.revision })
      return choices
    }
    return decided.revision === record.revision ? choices : null
  }

  /** Drop every pending proposal: "It looks right" writes a review, not the corrections on screen. */
  function discardProposals() {
    issue = null; correction = null; noneSelected = false
    written = data?.label ?? ''; writtenDirty = false
    box = null; editingBox = false; form = null
  }

  /**
   * Leave this occurrence without judging it: no character, no crop, no review, nothing written.
   *
   * The proposals on screen are dropped rather than kept, because a queued write is still a write.
   * A reader who wants the change kept saves it; Skip is the way to say "not this one" and move on.
   */
  function skip() {
    if (busy) return
    discardProposals()
    if (onskip) { onskip(); return }
    // A caller that takes verdicts is told this was a skip rather than a decision: `{ skip: true }`
    // is not a verdict, and a caller that ignores it simply gets no choice recorded for the crop.
    if (onVerdict) { onVerdict({ skip: true }); close(); return }
    if (advancing) { next(); return }
    close()
  }

  async function save(matches = false) {
    if (busy || !data || !fresh || !loaded || imageFailed) return
    // A replaced crop is saved as the crop on screen, not the retired one the link named.
    const target = data.id ?? id, current = generation
    if (matches) discardProposals()
    // The decision already saved is not sent again.
    if (settled) { if (advancing) { leaving = target; next() } else close(); return }
    // A bad crop redrawn here is fixed by the save, so the crop is reviewed with its new box.
    const fixed = issue === 'crop' && box
    const value = matches || !issue || fixed ? { verdict: 'match' } : { ...decision(issue), correction }
    const decided = value.verdict === 'match' ? { issue: null, correction: null, noneSelected: false, written: null } : choicesNow()
    if (onVerdict) {
      // The round gets the identity in its own field: a character the reader chose is `character`.
      const identity = writtenDirty && written && written !== data.label ? { character: written } : {}
      onVerdict(value.verdict === 'match' ? { unselect: true } : { ...value, ...identity, noneSelected })
      close()
      return
    }
    busy = true; error = ''
    // A chosen form is written first, and the review that follows names the revision it left. A wrong
    // character names the crop's character itself, so a form chosen beside it is not written.
    if (form != null && issue !== 'character') {
      try {
        const formed = await setForm(data, form)
        changed?.(target, formed.crop)
        if (closed || current !== generation) return
        data = { ...data, ...formed.crop }; form = null
        // A form that named the crop's character was a review already; nothing more to say.
        if (formed.reviewed && (matches || !issue)) { inspector.remember(target, { choices: decided }); leaving = advancing ? target : null; saved(target, formed.crop); busy = false; return }
      } catch (e) { if (!closed && current === generation) error = e.message; busy = false; return }
    }
    const correctingCharacter = writtenDirty && Boolean(written) && written !== data.label
    // A match, and a crop fixed by its redrawn box, carry no issue.
    const resolvedIssue = value.verdict === 'match' ? null : issue
    // Two routes with two contracts: the character editor takes the review request shape, and the
    // layer route takes the layers it records and nothing else (it forbids extra fields). The payload
    // is built for the route it is sent to rather than passed through from the other one.
    const route = correctingCharacter
      ? '/layers/units/' + encodeURIComponent(target)
      : '/atlas/characters/' + encodeURIComponent(target)
    const payload = correctingCharacter
      ? { revision: data.revision, image_sha256: data.image_sha256,
          verdict: matches ? 'match' : decision(issue || 'character').verdict,
          issue: ['character', 'crop', 'merged', 'blank', 'other'].includes(issue)
            ? issue : 'character',
          character: written }
      : { revision: data.revision, image_sha256: data.image_sha256,
          ...value, issue: resolvedIssue, ...(fixed ? { box } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    try {
      leaving = advancing ? target : null
      const result = await request(route, { id: submission.id, ...payload })
      inspector.remember(target, { choices: decided })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
  // A style saved for one crop may come back after the dialog has moved to another; the crop is
  // marked as changed either way, and the record on screen is replaced only when it is that crop.
  function styled(result) {
    changed?.(result.id, result)
    // A style leaves the decision as it was.
    const decided = inspector.decided(result.id)
    if (decided?.revision != null && decided.revision === data?.revision && result.id === data.id) inspector.remember(result.id, { ...decided, revision: result.revision })
    if (!closed && result.id === data?.id) data = result
  }
  /** A line correction changed this crop and its neighbours: the lists behind the dialog hear of each, and this crop is read again. */
  async function relined(result) {
    const target = data.id
    for (const { target_id } of result.results) if (target_id !== target) character(target_id, { wait: false }).then(record => changed?.(target_id, record), () => {})
    const record = await readCrop(target)
    if (closed || data?.id !== target) return
    data = record; issue = null; correction = null; noneSelected = false; form = null; kept = null; submission = null
    written = record.label ?? ''; writtenDirty = false
    changed?.(target, record)
  }
  /** Leave the editing, keeping the box, with the focus on what follows it. */
  async function endCrop() {
    editingBox = false
    await tick()
    ;(dialog?.querySelector('.crop-change button') ?? saveButton)?.focus({ preventScroll: true })
  }
  // A bad crop is redrawn in the page view as it stands: nothing reframes or moves, and the view takes
  // the keyboard so the arrows adjust the box at once.
  async function beginCrop() {
    editingBox = true
    await tick()
    figure?.querySelector('.crop-viewport')?.focus({ preventScroll: true })
    figure?.scrollIntoView({ block: 'nearest', behavior: 'instant' })
  }
  /** A box the page view drew, in its source pixels. */
  function edited(source, mode = 'resize') {
    if (!editingBox || busy) return
    box = fromEdit(data, source, mode)
  }
</script>

<svelte:window onkeydown={stepKey} />
<dialog class="character-dialog" bind:this={dialog} open oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label={t('character.dialog.label')}>
  <div class="inspector">
    <header class="inspector-header">{#if data}<CopyId id={data.id}>{#if fresh}<Cite entry={cropEntry(data, 'collection')} />{/if}</CopyId>{/if}<div class="inspector-navigation">{#if data}<FavouriteButton id={data.id} />{/if}<span>{position}</span><button class="icon-button previous-character" aria-label={t('common.previousCharacter')} disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label={t('common.nextCharacter')} disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label={t('common.closeReviewer')} onclick={close}>×</button></div></header>
    <!-- What the page has to say before the crop: a replacement, a cited earlier cut, a failed load. -->
    <div class="inspector-notices">{#if replaced}<p class="replaced-note" role="status">{t('character.replaced')}</p>{/if}
      {#if cited && !cited.current && cited.id === data?.id}<CitedCut {cited} />{/if}
      {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>{t('character.reload')}</button></div>{/if}</div>
    {#if data}
      <!-- The crop alone, in a box of one size for every crop, then the page around it further down. -->
      <div class="inspector-page">
        <div class="inspector-figure" bind:this={figure}>
          {#key data.image}<CropContext item={data} detail={data} cropBox={drawn ? toSource(drawn) : null} disabled={busy} editing={editingBox} onedit={edited} onexit={endCrop} describedby="crop-keys"
            onload={() => { loaded = true; imageFailed = false }} onerror={() => imageFailed = true} />{/key}
          {#if editingBox}<p class="crop-keys" id="crop-keys">{t('character.crop.keys')}<button type="button" disabled={busy} onclick={endCrop}>{t('character.crop.doneAdjusting')}</button></p>{/if}
        </div>
        <div class="credit-beside"><SourceCredit item={data} /></div>
      </div>
      <div class="inspector-right">
        <div class="inspector-production">{#if productionLabel(data)}<ProductionBadge item={data} />{/if}<StyleField item={data} editable={!onVerdict} disabled={busy || !fresh} working={value => busy = value} saved={styled} /></div>
        <div class="inspector-title"><CropTitle char={data.label} script={data.script} /><ZiLink character={data.label} />{#if data.repair?.reason}<span class="repair-note" title={data.repair.reason}>{data.repair.withheld ? t('repair.withheld') : data.repair.verified ? t('repair.checked') : t('repair.machine')}</span>{:else if repairOf(data)?.label === 'no-class'}<span class="repair-note" title={t('repair.reason.noClass')}>{t('repair.noClass')}</span>{/if}{#if data.box_pending}<span class="state-pill">{t('character.crop.pending')}</span>{:else if data.state === 'checked' || data.state === 'flagged'}<span class="state-pill" class:flagged={data.state === 'flagged'}>{data.state === 'checked' ? t('state.checked') : t('state.flagged')}</span>{/if}</div>
        {#if !onVerdict && (issue === 'character' || issue === 'crop')}<LineStrip id={data.id} ready={fresh && loaded} disabled={busy || !fresh} working={value => busy = value} saved={relined} />{/if}
        {#snippet formBar()}{#if !onVerdict}<CropForm crop={data} chosen={form} onchoose={value => form = value} disabled={busy || !fresh} />{/if}{/snippet}
        <CropReview forms={formBar} {issue} onissue={chooseIssue} suggested={suggestedIssue} disabled={busy || !fresh} onskip={skip}
          targetId={data.id} bind:element={suggestionsElement} {noneSelected} result={suggestions} loading={suggesting} contextResult={contextSuggestions} contextLoading={contextSuggesting} label={data.label} value={issue === 'character' ? written : correction} onchoose={chooseSuggestion} />
        {#if issue === 'crop' && !onVerdict && data.context && data.crop_editable !== false && (box || !editingBox)}<div class="crop-change">{#if box}{t('character.crop.adjusted')}<button type="button" disabled={busy} onclick={() => box = null}>{t('common.reset')}</button>{:else}<button type="button" class="quiet-link adjust-crop" disabled={busy || editingBox} onclick={beginCrop}>{t('character.crop.adjust')}</button>{/if}</div>{/if}
        <SimilarCrops id={data.id} label={data.label} ready={fresh && loaded} />
      </div>
      <div class="credit-after"><SourceCredit item={data} /></div>
    {:else if !error}<div class="inspector-page"><div class="inspector-figure shimmer"></div></div><div class="inspector-right"><div class="inspector-skeleton"></div></div>{/if}
  </div>
  <footer class="inspector-savebar">
    <!-- Always there outside a round, so the choice is visible before a list is opened. -->
    {#if !onVerdict}<AdvanceSwitch disabled={busy} />{/if}
    {#if imageFailed}<span role="alert">{t('character.image.unavailable')}</span>{/if}
    <button class="primary save-character" class:chosen={settled} bind:this={saveButton} disabled={busy || !data || !fresh || !loaded || imageFailed} onclick={() => save()}>{busy ? t('common.saving') : (issue === 'crop' && box) || (!issue && form != null) ? t(advancing ? 'character.save.changes.next' : 'character.save.changes.close') : issue ? (onVerdict ? t('character.save.useError') : t(advancing ? 'character.save.issue.next' : 'character.save.issue.close')) : (onVerdict ? t('character.save.backToSelection') : t(advancing ? 'character.save.looksRight.next' : 'character.save.looksRight.close'))} {#if onVerdict || advancing}<span>{settled ? '✓ →' : '→'}</span>{:else if !issue || settled}<span>✓</span>{/if}</button>
    {#if issue}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => { discardProposals(); save(true) }}>{onVerdict ? t('character.save.removeSelection') : t('character.save.itLooksRight')}</button>{/if}
    <ContributionTerms />
  </footer>
</dialog>

<style>
  .crop-keys{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0 0;font-size:10px;color:var(--muted)}
  .crop-keys button{font-size:10px;padding:4px 8px}
</style>
