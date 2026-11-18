<script module>
  const submissions = new Map()
</script>

<script>
  // One occurrence of a run, opened from Explore or a sequence page: the run on its page with the
  // page around it (the whole context render it was cut from), a link to the sequence with the same
  // graphemes, its characters to approve or fix one by one, and where it comes from, with a link to
  // the source: the dataset's own viewer at the place for a corpus glyph, the transcription page on
  // みんなで翻刻 for a crop transcribed there.
  import { onMount, tick } from 'svelte'
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import SourceCredit from './SourceCredit.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import ReviewState from './ReviewState.svelte'
  import Glyph from './Glyph.svelte'
  import ContributionTerms from './ContributionTerms.svelte'
  import { sourceLinks } from '../lib/licence.js'
  import { character, cropPixels, request } from '../lib/client.js'
  import { forgetCrop } from '../lib/cropCache.js'
  import { useInspector } from '../lib/inspector.svelte.js'
  import { isUnassigned, kanaFolded } from '../lib/identity.js'
  import { runCharacters } from '../lib/runs.js'
  import { approveCropRequest, approvedCropPatch, canApproveCrop, occurrenceWritten } from '../lib/sequenceReview.js'
  import { t, around } from '../lib/i18n.svelte.js'

  let {
    occurrence,
    text,
    count = null,
    more = null,
    onmore = null,
    previous = null,
    next = null,
    position = '',
    inspect,
    close,
    onupdated = null,
  } = $props()

  const inspector = useInspector()
  let dialog
  let activeCrop = $state(null), busyId = $state(null), error = $state(''), closed = false

  const writtenInfo = $derived(occurrenceWritten(occurrence.crops, text))
  const forms = $derived(writtenInfo?.forms ?? null)
  const written = $derived(writtenInfo?.written ?? text)
  const differs = $derived(kanaFolded(written) !== kanaFolded(text))
  const parts = $derived(runCharacters(text))
  const checkedCount = $derived(occurrence.crops.filter(crop => crop.state === 'checked').length)
  const anyApprovable = $derived(occurrence.crops.some(crop => crop.state !== 'checked' && canApproveCrop(crop)))

  // The occurrence's みんなで翻刻 page (`honkoku_url`) is the first its crops resolve to.
  const lead = $derived({ ...occurrence.crops[0], honkoku_url: occurrence.honkoku_url })
  const corpus = $derived(lead.origin === 'corpus')
  const source = $derived.by(() => { const links = sourceLinks(lead, corpus); return corpus ? links.record : links.honkoku })
  // The credit names the work, its holder, licence and date; the page is said apart.
  const page = $derived(lead.page_number ? t('tile.page', { page: lead.page_number }) : '')

  const ISSUE_TITLES = {
    character: 'issue.character.title',
    merged: 'issue.merged.title',
    crop: 'issue.crop.title',
    blank: 'issue.blank.title',
  }
  const cropLabel = crop => crop.written_character ?? crop.label ?? ''
  const stateTone = crop => crop.state === 'checked' ? 'checked'
    : crop.state === 'flagged' ? 'flagged'
    : crop.state === 'hard' || crop.needs_segmentation ? 'waiting'
    : 'unchecked'
  function stateText(crop) {
    if (crop.state === 'checked') return t('state.checked')
    if (crop.state === 'flagged') return ISSUE_TITLES[crop.issue] ? t(ISSUE_TITLES[crop.issue]) : t('state.flagged')
    if (crop.state === 'hard') return t('state.hard')
    if (crop.origin === 'corpus' && crop.needs_segmentation) return t('corpus.state.needsSplitting')
    return t('state.unchecked')
  }

  function submissionFor(crop) {
    const sig = `${crop.id}:${crop.revision}:${Object.values(cropPixels(crop))[0]}`
    if (!submissions.has(sig)) submissions.set(sig, crypto.randomUUID())
    return { sig, id: submissions.get(sig) }
  }

  async function approve(crop, index) {
    if (busyId !== null || !canApproveCrop(crop) || crop.state === 'checked') return
    const rowHadFocus = dialog?.contains(document.activeElement) && document.activeElement?.closest('[data-crop-index]')?.dataset.cropIndex === String(index)
    busyId = crop.id; error = ''
    let sub = null, focusNext = false
    try {
      let target = crop
      if (!Object.keys(cropPixels(target)).length) {
        const loaded = await character(target.id)
        const changed = cropLabel(loaded) !== cropLabel(crop) || (crop.revision != null && loaded.revision !== crop.revision)
        if (changed || loaded.state === 'checked') {
          onupdated?.(target.id, loaded)
          focusNext = loaded.state === 'checked'
          return
        }
        target = { ...target, ...cropPixels(loaded) }
      }
      sub = submissionFor(target)
      const { route, body } = approveCropRequest(target, sub.id)
      const result = await request(route, body)
      submissions.delete(sub.sig)
      const patch = approvedCropPatch(target, result)
      inspector?.remember(target.id, {
        choices: { issues: [], correction: null, noneSelected: false, written: null },
        revision: patch.revision,
      })
      forgetCrop(target.id)
      onupdated?.(target.id, patch)
      focusNext = true
    } catch (e) {
      if (sub && e?.status >= 400 && e?.status < 500) submissions.delete(sub.sig)
      if (!closed) error = e.message
      if (e?.status === 409) {
        const fresh = await character(crop.id).catch(() => null)
        if (fresh) {
          onupdated?.(crop.id, fresh)
          if (fresh.state === 'checked') focusNext = true
        }
      }
    } finally {
      if (!closed) busyId = null
    }
    const focusUnchanged = !dialog?.contains(document.activeElement) || document.activeElement?.closest('[data-crop-index]')?.dataset.cropIndex === String(index)
    if (focusNext && rowHadFocus && focusUnchanged && !closed) {
      await tick()
      const nextIndex = occurrence.crops.findIndex((c, idx) => idx > index && c.state !== 'checked')
      const targetIndex = nextIndex >= 0 ? nextIndex : occurrence.crops.findIndex((c, idx) => idx !== index && c.state !== 'checked')
      const button = targetIndex >= 0
        ? dialog?.querySelector(`[data-crop-index="${targetIndex}"] ${canApproveCrop(occurrence.crops[targetIndex]) ? '.crop-approve' : '.crop-fix'}`)
        : dialog?.querySelector(`[data-crop-index="${index}"] .crop-fix`)
      if (button) {
        button.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        button.focus({ preventScroll: true })
      }
    }
  }

  const open = crop => inspect(crop.id)

  function stepKey(event) {
    if (event.defaultPrevented || busyId !== null || event.metaKey || event.ctrlKey || event.altKey) return
    if (event.target.closest?.('input, textarea, select')) return
    if ([...document.querySelectorAll('dialog[open]')].at(-1) !== dialog) return
    if (event.key === 'ArrowLeft' && previous) { event.preventDefault(); previous() }
    else if (event.key === 'ArrowRight' && next) { event.preventDefault(); next() }
  }

  let shownKey = null
  $effect(() => {
    const key = occurrence.crops[0]?.id ?? ''
    if (key === shownKey) return
    const stepped = shownKey !== null
    shownKey = key
    activeCrop = null; error = ''
    dialog?.scrollTo({ top: 0 })
    if (stepped && dialog && !dialog.contains(document.activeElement)) {
      tick().then(() => {
        if (!closed && !dialog.contains(document.activeElement)) {
          dialog.querySelector('.occurrence-crop-row .crop-approve, .occurrence-crop-row .crop-fix')?.focus({ preventScroll: true })
        }
      })
    }
  })

  onMount(() => {
    dialog.showModal()
    return () => { closed = true }
  })
</script>

<svelte:window onkeydown={stepKey} />

<dialog class="run-occurrence-dialog" bind:this={dialog} oncancel={e => { e.preventDefault(); close() }}
        onclick={e => { if (e.target === dialog) close() }} aria-labelledby="run-occurrence-title">
  <header class="occurrence-header">
    <div class="occurrence-heading">
      <h2 id="run-occurrence-title">
        {#if forms}
          {#each forms as form, i (i)}<span class:transcribed={form.transcribed}><ScriptText text={form.text} titled={false} /></span>{/each}
        {:else}
          <ScriptText {text} />
        {/if}
      </h2>
      {#if more}
        <a class="run-sequence-link" href={more} onclick={e => { if (!e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && e.button === 0) { if (onmore) { e.preventDefault(); onmore() } close() } }}>
          <span>{around('run.showSequence', 'sequence')[0]}<ScriptText {text} titled={false} />{around('run.showSequence', 'sequence')[1]}</span>
          {#if count != null}<small>{t('run.occurrences', { count })}</small>{/if}
          <span aria-hidden="true">→</span>
        </a>
      {:else if differs}
        <span class="run-graphemes" title={t('chips.grapheme')}><ScriptText {text} /></span>
      {/if}
    </div>
    <div class="occurrence-nav">
      {#if previous || next}
        <span>{position}</span>
        <button type="button" class="icon-button previous-occurrence" aria-label={t('run.detail.previous')} disabled={busyId !== null || !previous} onclick={() => previous?.()}>←</button>
        <button type="button" class="icon-button next-occurrence" aria-label={t('run.detail.next')} disabled={busyId !== null || !next} onclick={() => next?.()}>→</button>
      {/if}
      <button type="button" class="icon-button close-occurrence" aria-label={t('run.detail.close')} onclick={close}>×</button>
    </div>
  </header>
  <div class="occurrence-body">
    <div class="occurrence-context">
      <figure class="occurrence-page"><RunImage crops={occurrence.crops} page={occurrence.page} vertical={occurrence.vertical} context active={activeCrop} oninspect={id => { const crop = occurrence.crops.find(c => c.id === id); if (crop) open(crop) }} /></figure>
      {#if page}<p class="occurrence-where">{page}</p>{/if}
      <SourceCredit item={lead} omit={source?.href} />
      {#if source}<a class="primary occurrence-source" href={source.href} target="_blank" rel="noreferrer">{source.label}</a>{/if}
    </div>
    <div class="occurrence-review">
      <div class="occurrence-chars-head">
        <h3>{t('run.chars.label')}</h3>
        <span aria-live="polite">{t('run.chars.progress', { checked: checkedCount, total: occurrence.crops.length })}</span>
      </div>
      {#if error}<div class="error-message" role="alert">{error}</div>{/if}
      <ol class="occurrence-crops">
        {#each occurrence.crops as crop, i (crop.id)}
          {@const label = cropLabel(crop)}
          {@const unassigned = isUnassigned(crop)}
          {@const expected = parts.length === occurrence.crops.length ? parts[i] : null}
          {@const state = crop.state}
          {@const canApprove = canApproveCrop(crop)}
          {@const isBusy = busyId === crop.id}
          <li
            class="occurrence-crop-row"
            class:checked={state === 'checked'}
            class:flagged={state === 'flagged' || state === 'hard'}
            class:active={activeCrop === i}
            data-crop-index={i}
            data-crop-id={crop.id}
            onpointerenter={() => activeCrop = i}
            onpointerleave={() => { if (activeCrop === i) activeCrop = null }}
            onfocusin={() => activeCrop = i}
            onfocusout={e => { if (!e.currentTarget.contains(e.relatedTarget) && activeCrop === i) activeCrop = null }}
          >
            <button type="button" class="crop-summary" onclick={() => open(crop)} aria-label={t('explore.tile.inspect', { label: unassigned ? t('corpus.unassigned') : label })}>
              {#if crop.image}<Glyph item={crop} alt="" class="occurrence-crop" />{/if}
              <span class="crop-identity">
                <span class="crop-written" class:unassigned>
                  {#if unassigned}
                    {t('corpus.unassigned')}
                  {:else}
                    <ReferenceGlyph char={label} code_point={crop.code_point ?? ''} script={crop.script} size="md" />
                  {/if}
                  {#if expected && (unassigned || kanaFolded(label) !== kanaFolded(expected))}
                    <span class="crop-grapheme" title={t('chips.grapheme')}><ScriptText text={expected} titled={false} /></span>
                  {/if}
                </span>
                <ReviewState tone={stateTone(crop)} text={stateText(crop)} />
              </span>
            </button>
            <div class="crop-actions">
              {#if canApprove && state !== 'checked'}
                <button type="button" class="crop-approve" disabled={busyId !== null} aria-label={`${t('run.char.approve')}: ${label}`} onclick={() => approve(crop, i)}>
                  {isBusy ? t('common.saving') : `✓ ${t('run.char.approve')}`}
                </button>
              {/if}
              <button type="button" class="crop-fix" disabled={busyId !== null} aria-label={`${state === 'checked' ? t('run.char.edit') : t('run.char.fix')}: ${unassigned ? t('corpus.unassigned') : label}`} onclick={() => open(crop)}>
                {state === 'checked' ? t('run.char.edit') : t('run.char.fix')}
              </button>
            </div>
          </li>
        {/each}
      </ol>
      {#if anyApprovable}<div class="occurrence-terms"><ContributionTerms /></div>{/if}
    </div>
  </div>
</dialog>

<style>
  .run-occurrence-dialog{width:min(880px,calc(100vw - 32px));max-height:calc(100dvh - 32px);overflow:auto;border:1px solid var(--line);border-radius:12px;padding:20px 24px;background:var(--surface);color:var(--ink);box-shadow:0 20px 60px var(--shadow)}
  .run-occurrence-dialog::backdrop{background:light-dark(#16151f40, rgb(0 0 0 / 50%))}
  .occurrence-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--line)}
  .occurrence-heading{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 14px;min-width:0}
  .occurrence-heading h2{margin:0;font-size:28px;font-weight:500;line-height:1.2}
  .transcribed{opacity:.5}
  .run-graphemes{font-size:15px;color:var(--muted)}
  .run-sequence-link{display:inline-flex;align-items:baseline;gap:6px;padding:5px 11px;border:1px solid var(--line);border-radius:999px;font-size:13px;color:var(--accent);background:var(--surface-subtle);text-decoration:none}
  .run-sequence-link:hover,.run-sequence-link:focus-visible{border-color:var(--accent);background:var(--accent-light)}
  .run-sequence-link small{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
  .occurrence-nav{display:flex;align-items:center;gap:4px;flex-shrink:0}
  .occurrence-nav>span{font-size:11px;color:var(--muted);margin-right:4px;font-variant-numeric:tabular-nums}
  .occurrence-nav .icon-button{width:32px;height:32px;padding:2px;font-size:20px;color:var(--muted);display:inline-flex;align-items:center;justify-content:center}
  .occurrence-nav .close-occurrence{font-size:24px}
  .occurrence-body{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:18px 24px;align-items:start}
  .occurrence-context,.occurrence-review{min-width:0}
  .occurrence-page{margin:0 0 12px;height:min(54dvh,460px);padding:10px;background:var(--surface-tile);border:1px solid var(--line);border-radius:8px}
  .occurrence-where{margin:0 0 4px;font-size:13px}
  .occurrence-source{display:inline-block;margin-top:12px;font-size:13px;padding:10px 16px;text-decoration:none}
  .occurrence-chars-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:10px}
  .occurrence-chars-head h3{margin:0;font-size:12px;font-weight:500;color:var(--muted)}
  .occurrence-chars-head span{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
  .occurrence-crops{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
  .occurrence-crop-row{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);transition:border-color .15s,background .15s}
  .occurrence-crop-row.checked{border-color:color-mix(in srgb,var(--good) 45%,var(--line));background:var(--good-light)}
  .occurrence-crop-row.flagged{border-color:color-mix(in srgb,var(--wrong) 45%,var(--line));background:var(--wrong-light)}
  .occurrence-crop-row.active{border-color:var(--accent)}
  .crop-summary{display:flex;align-items:center;gap:10px;flex:1;min-width:0;padding:0;border:0;background:transparent;text-align:left;border-radius:6px}
  .crop-summary :global(.occurrence-crop){width:44px;height:44px;flex:none;border-radius:4px}
  .crop-identity{display:flex;flex-direction:column;align-items:flex-start;gap:4px;min-width:0}
  .crop-written{display:flex;align-items:baseline;gap:6px;font-size:20px;line-height:1.2}
  .crop-written.unassigned{font-size:13px;color:var(--muted)}
  .crop-grapheme{font-size:13px;color:var(--muted)}
  .crop-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}
  .crop-approve,.crop-fix{padding:7px 11px;font-size:12px;border-radius:7px;background:var(--surface);white-space:nowrap}
  .crop-approve{font-weight:500;color:var(--good);border-color:color-mix(in srgb,var(--good) 50%,var(--line))}
  .crop-approve:hover:not(:disabled){background:var(--good-light);border-color:var(--good)}
  .occurrence-terms{margin-top:12px}
  .occurrence-terms :global(.contribution-terms){text-align:left}
  @media(max-width:759px){
    .run-occurrence-dialog{padding:16px 18px}
    .occurrence-body{grid-template-columns:1fr;gap:16px}
    .occurrence-page{height:min(44dvh,360px)}
    .occurrence-crop-row{flex-wrap:wrap}
    .crop-actions{margin-left:auto}
  }
</style>
