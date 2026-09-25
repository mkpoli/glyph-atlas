<script>
  import ProductionBadge from './ProductionBadge.svelte'
  import ZiLink from './ZiLink.svelte'
  import { onMount, untrack, tick } from 'svelte'
  import { character, request, suggestionsFor } from '../lib/client.js'
  import { decision, isSingle, suggestsReading, greetSuggestions, skipHint } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  import CropContext from './CropContext.svelte'
  import IssuePicker from './IssuePicker.svelte'
  import ReadingSuggestions from './ReadingSuggestions.svelte'
  // `onskip` is supplied by the caller that owns the queue. The dialog never decides what "next"
  // means: it reports that the reader declined to judge this occurrence, and the caller advances,
  // closes, or does something else. Without the prop the sensible default is the same as finishing
  // with it — the next occurrence if the caller offered one, otherwise close.
  let { id, clientId, close, saved, onVerdict = null, onskip = null,
        previous = null, next = null, position = '' } = $props()
  let dialog, data = $state(null), error = $state(''), busy = $state(false)
  let reading = $state(''), note = $state(''), issue = $state(null), noneSelected = $state(false), correction = $state(null)
  // The character the source printed and the reading it has are two layers: this holds the encoded
  // written identity, which is corrected on `unicode`, while `reading` is corrected on `reading`.
  // A phonetic edit never rewrites the identity, and this never rewrites the reading. Each field
  // carries whether a reviewer touched it, because a field nobody edited must not be written: on
  // this corpus 340 units are written in hiragana and read in katakana, so the label is not the
  // reading and saving one over the other would silently rewrite the layer nobody looked at.
  let written = $state(''), writtenDirty = $state(false), readingDirty = $state(false)
  let editingBox = $state(false), box = $state(null), start = null, contextElement = $state(null)
  let suggestions = $state(null), suggesting = $state(false), loaded = $state(false), imageFailed = $state(false)
  let contextSuggestions = $state(null), contextSuggesting = $state(false)
  let closed = false, generation = 0, submission = null, nearby = $state(null), suggestionsElement = $state(null)
  // The character and the strokes around it come from one source image at one revision, so the
  // outline is the crop's own rectangle as a fraction of the context rectangle, both reported by
  // the server from the bounds it cut. A client-side adjustment replaces the rectangle.
  // An edit is held in page pixels, because that is what the API stores and validates; the context
  // view measures source pixels, because that is what the crop was cut from. The two are the same
  // rectangle only when the cached image is the page's own size, so the scale converts between them.
  const scale = $derived(data?.source_scale || [1, 1])
  const toSource = (b) => ({ x: b.x * scale[0], y: b.y * scale[1], w: b.w * scale[0], h: b.h * scale[1] })
  const boxStyle = $derived(data?.context_box ? (() => {
    const c = data.context_box, b = box ? toSource(box) : data.crop_box
    if (!b) return ''
    return `left:${100 * (b.x - c.x) / c.w}%;top:${100 * (b.y - c.y) / c.h}%;width:${100 * b.w / c.w}%;height:${100 * b.h / c.h}%`
  })() : '')
  // NDL reads lines, so a confident reading longer than one character hints at a merged crop.
  // Results stored before votes were recorded carry NDL's reading only among the candidates.
  const lineReading = $derived([...(suggestions?.votes || []), ...(suggestions?.candidates || [])].find(vote => vote.engine === 'NDLkotenOCR'))
  const suggestedIssue = $derived(lineReading && lineReading.score >= .65 && !isSingle(lineReading.text) ? 'merged' : null)
  let replaced = $state(false)
  async function load(target, redirected = false) {
    const current = ++generation
    replaced = redirected
    dialog?.scrollTo({ top: 0 })
    data = null; error = ''; issue = null; correction = null; noneSelected = false; note = ''; box = null; start = null; editingBox = false
    written = ''; writtenDirty = false; readingDirty = false
    contextSuggestions = null; contextSuggesting = false
    loaded = false; imageFailed = false; suggestions = null; suggesting = false; submission = null
    try {
      const result = await character(target)
      if (closed || current !== generation) return
      data = result
      // `label` is the character the record was written with and `reading` is what it reads; the
      // reading field starts at the reading, and falls back to the label only for a record that has
      // none, so an untouched save writes neither.
      reading = result.reading ?? result.label
      written = result.label ?? ''
      contextSuggesting = true
      suggestionsFor(result, 'context').then(value => { if (!closed && current === generation) { contextSuggestions = value; contextSuggesting = false } })
      suggesting = true
      suggestionsFor(result).then(value => { if (!closed && current === generation) { suggestions = value; suggesting = false } })
    } catch (e) {
      if (closed || current !== generation) return
      if (e.replacedBy) {
        // A link to a retired crop opens the crop that replaced it, and the address follows.
        const link = '#/character/' + encodeURIComponent(target)
        if (location.hash === link) history.replaceState(history.state, '', '#/character/' + encodeURIComponent(e.replacedBy))
        return load(e.replacedBy, true)
      }
      error = e.message
    }
  }
  $effect(() => { const target = id; untrack(() => load(target)) })
  onMount(() => { dialog.showModal(); return () => { closed = true; generation++ } })
  function chooseIssue(value) {
    if (issue === 'character') { written = data?.label ?? ''; writtenDirty = false }
    issue = value; correction = null; noneSelected = false; submission = null
    // The suggestion area appears with this choice, so the next action is the one focused. An issue
    // with no suggestions moves nothing, and no later arrival takes the focus back.
    if (!suggestsReading(value)) return
    queueMicrotask(() => greetSuggestions(suggestionsElement, { focus: true }))
  }
  /** A suggestion that is one character names the character, so it corrects the written identity. */
  function chooseSuggestion(value, none = false) {
    noneSelected = none
    submission = null
    if (!value && issue === 'character') {
      written = data?.label ?? ''; writtenDirty = false; issue = 'reading'
    }
    if (value && isSingle(value)) {
      // One character names the character: the chosen value is carried as the written identity, and
      // the suggestion list highlights it from `written` rather than from `correction`. A round
      // refuses reading text on a character issue, so nothing goes into `correction`, and the reading
      // the record already had is left exactly as it was.
      written = value; writtenDirty = true; correction = null; noneSelected = false; issue = 'character'
      return
    }
    correction = value
  }

  /** Drop every pending proposal: "It looks right" writes a review, not the corrections on screen. */
  function discardProposals() {
    issue = null; correction = null; noneSelected = false
    written = data?.label ?? ''; writtenDirty = false
    reading = data?.reading ?? data?.label ?? ''; readingDirty = false
    box = null
  }

  /**
   * Leave this occurrence without judging it: no reading, no crop, no review, nothing written.
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
    if (next) { next(); return }
    close()
  }

  async function save(matches = false) {
    if (busy || !data || !loaded || imageFailed) return
    // A replaced crop is saved as the crop on screen, not the retired one the link named.
    const target = data.id ?? id, current = generation
    if (matches) discardProposals()
    const value = matches || !issue ? { verdict: 'match' } : { ...decision(issue), correction }
    if (onVerdict) {
      // The round gets the identity in its own field, and never as a reading: a character the reader
      // chose is `character`, and the reading it already had stays untouched.
      const identity = writtenDirty && written && written !== data.label ? { character: written } : {}
      onVerdict(value.verdict === 'match' ? { unselect: true } : { ...value, ...identity, noneSelected })
      close()
      return
    }
    busy = true; error = ''
    const correctingCharacter = writtenDirty && Boolean(written) && written !== data.label
    const readingEdit = readingDirty && reading !== data.reading ? { reading } : {}
    // `/atlas/characters` records reading issues; a character issue with no new character is one.
    const resolvedIssue = matches || (issue === 'character' && !correctingCharacter) ? 'reading'
      : issue || (correctingCharacter ? 'character' : 'reading')
    // Two routes with two contracts: the character editor takes the review request shape, and the
    // layer route takes the layers it records and nothing else (it forbids extra fields). The payload
    // is built for the route it is sent to rather than passed through from the other one.
    const route = correctingCharacter
      ? '/layers/units/' + encodeURIComponent(target)
      : '/atlas/characters/' + encodeURIComponent(target)
    const payload = correctingCharacter
      ? { client_id: clientId, revision: data.revision, image_sha256: data.image_sha256,
          verdict: matches ? 'match' : decision(issue || 'character').verdict,
          issue: ['character', 'reading', 'crop', 'merged', 'blank', 'other'].includes(issue)
            ? issue : 'character',
          note, character: written, ...readingEdit, ...(box ? { box } : {}) }
      : { client_id: clientId, revision: data.revision, image_sha256: data.image_sha256,
          ...value, issue: resolvedIssue, note, ...readingEdit, ...(box ? { box } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    try {
      const result = await request(route, { id: submission.id, ...payload })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
  async function beginCrop() {
    editingBox = true
    await tick()
    // The stroke view is small and sits under the crop, so adjusting means looking at it: bring it
    // into view rather than leaving the reader to find it. No focus is taken, since the drag follows.
    nearby?.scrollIntoView({ block: 'nearest', behavior: 'instant' })
  }
  function point(e) {
    // The pointer's place in the context view, as a page pixel: the view is the source rectangle, so
    // its fraction is the fraction of the page rectangle the scale corresponds to.
    const r = contextElement.getBoundingClientRect(), c = data.context_box
    const fx = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))
    const fy = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))
    return { x: Math.round((c.x + fx * c.w) / scale[0]), y: Math.round((c.y + fy * c.h) / scale[1]) }
  }
  function down(e) { if (!editingBox || !data.context_box || busy) return; e.preventDefault(); start = point(e); contextElement.setPointerCapture(e.pointerId) }
  function move(e) {
    if (!start) return
    // `point` answers in page pixels and so does the drag, so the new box is a page box; the outline
    // converts it back to the view's pixels to draw it.
    const end = point(e), limits = pageBounds()
    const x = Math.min(limits.x + limits.w - 1, Math.min(start.x, end.x))
    const y = Math.min(limits.y + limits.h - 1, Math.min(start.y, end.y))
    box = { x, y,
      w: Math.min(limits.x + limits.w - x, Math.max(1, Math.abs(end.x - start.x))),
      h: Math.min(limits.y + limits.h - y, Math.max(1, Math.abs(end.y - start.y))) }
  }
  /** The context rectangle in page pixels, which is what a drag is bounded by. */
  function pageBounds() {
    const c = data.context_box
    return { x: c.x / scale[0], y: c.y / scale[1], w: c.w / scale[0], h: c.h / scale[1] }
  }
</script>

<dialog class="character-dialog" bind:this={dialog} oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label={t('character.dialog.label')}>
  <div class="inspector">
    <header class="inspector-header"><span class="overline">{t('character.overline')}</span><div class="inspector-navigation"><span>{position}</span><button class="icon-button previous-character" aria-label={t('common.previousCharacter')} disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label={t('common.nextCharacter')} disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label={t('common.closeReviewer')} onclick={close}>×</button></div></header>
    {#if replaced}<p class="replaced-note" role="status">{t('character.replaced')}</p>{/if}
    {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>{t('character.reload')}</button></div>{/if}
    {#if data}
      <div class="inspector-production"><ProductionBadge item={data} /></div>
      <div class="inspector-title"><h2 lang="ja">{data.label}</h2><ZiLink character={data.label} />{#if data.repair?.reason}<span class="repair-note" title={data.repair.reason}>{data.repair.withheld ? t('repair.withheld') : data.repair.verified ? t('repair.checked') : t('repair.machine')}</span>{/if}<span class="state-pill" class:flagged={data.state === 'flagged'}>{data.state === 'checked' ? t('state.checked') : data.state === 'flagged' ? t('state.flagged') : t('state.unreviewed')}</span></div><p class="record-id"><code>{data.id}</code><button type="button" class="copy-id" onclick={() => navigator.clipboard?.writeText(data.id)} aria-label={t('inspector.copyId')}>{t('inspector.copyId')}</button></p>
      <div class="inspector-figure">
        {#if editingBox && data.context && data.context_box}
          <figure class="nearby crop-adjustment" bind:this={nearby}>
            <div class="context-region drawing" bind:this={contextElement} onpointerdown={down} onpointermove={move} onpointerup={() => start = null} onpointercancel={() => start = null} role="img" aria-label={t('character.crop.dragToAdjust')}>
              <img src={data.context_image} alt={t('character.context.alt')} draggable="false" onload={() => { loaded = true; imageFailed = false }} onerror={() => { editingBox = false; error = t('character.context.loadError') }} />
              {#if boxStyle}<span class="context-outline" style={boxStyle}></span>{/if}
            </div>
            <figcaption><button type="button" disabled={busy} onclick={() => editingBox = false}>{t('character.crop.doneAdjusting')}</button></figcaption>
          </figure>
        {:else}
          {#key data.image}<CropContext item={data} detail={data} cropBox={box ? toSource(box) : null} disabled={busy} onload={() => { loaded = true; imageFailed = false }} onerror={() => imageFailed = true} />{/key}
        {/if}
      </div>
      {#if data.licence}<div class="image-credit"><span>{data.source}</span><small>{[data.attribution || data.holder, data.licence].filter(Boolean).join(' · ')}</small>{#if data.rights_url}<a href={data.rights_url} target="_blank" rel="noreferrer">{t('character.sourceRights')}</a>{/if}</div>{/if}
      <div class="inspector-question"><strong>{t('character.question.whatsWrong')}</strong><span>{t('character.question.chooseOne')}</span></div>
      <IssuePicker value={issue} choose={chooseIssue} suggested={suggestedIssue} disabled={busy} />
      <ReadingSuggestions targetId={data.id} bind:element={suggestionsElement} {noneSelected} result={suggestions} loading={suggesting} contextResult={contextSuggestions} contextLoading={contextSuggesting} {issue} reading={data.label} value={issue === 'character' ? written : correction} disabled={busy} choose={chooseSuggestion} />
      {#if !onVerdict}<details class="advanced-edit"><summary>{t('character.advancedEdit.summary')}</summary><label class="written-input">{t('character.field.character')}<input lang="ja" aria-label={t('character.field.character.aria')} bind:value={written} oninput={() => writtenDirty = true} maxlength="8" disabled={busy} placeholder={data.label} /></label>{#if writtenDirty && written && written !== data.label}<p class="written-note" role="status">{t('character.field.character.note')}</p>{/if}<label class="reading-input">{t('character.field.reading')}<input lang="ja" aria-label={t('character.field.reading.aria')} bind:value={reading} oninput={() => readingDirty = true} maxlength="32" disabled={busy} /></label>{#if data.context && data.crop_editable !== false}<button type="button" class="quiet-link adjust-crop" onclick={beginCrop}>{t('character.crop.adjust')}</button>{/if}<textarea aria-label={t('character.note.aria')} bind:value={note} rows="2" maxlength="2000" placeholder={t('character.note.placeholder')} disabled={busy}></textarea>{#if box}<div class="crop-change">{t('character.crop.adjusted')}<button type="button" onclick={() => box = null}>{t('common.reset')}</button></div>{/if}</details>{/if}
    {:else if !error}<div class="inspector-skeleton"></div>{/if}
  </div>
  <footer class="inspector-savebar">
    {#if imageFailed}<span role="alert">{t('character.image.unavailable')}</span>{/if}
    <button class="primary save-character" disabled={busy || !data || !loaded || imageFailed} onclick={() => save()}>{busy ? t('common.saving') : issue ? (onVerdict ? t('character.save.useError') : next ? t('character.save.issueNext') : t('character.save.issue')) : (onVerdict ? t('character.save.backToSelection') : next ? t('character.save.looksRightNext') : t('character.save.looksRight'))} <span>{issue || onVerdict ? '→' : '✓'}</span></button>
    {#if issue}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => { discardProposals(); save(true) }}>{onVerdict ? t('character.save.removeSelection') : t('character.save.itLooksRight')}</button>{/if}
    <button class="skip-character" disabled={busy} onclick={skip} title={skipHint()}>{t('common.skip.arrow')}</button>
  </footer>
</dialog>

<style>
  .image-credit{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted);margin:12px 0 24px}.image-credit a{color:inherit}
  .crop-adjustment{flex:1 1 100%;width:100%;gap:10px}
  .crop-adjustment .context-region{max-height:360px}
  .crop-adjustment .context-region img{max-height:360px;filter:none}
</style>
