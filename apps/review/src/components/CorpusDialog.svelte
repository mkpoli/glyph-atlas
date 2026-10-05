<script>
  import ContributionTerms from './ContributionTerms.svelte'
  import SimilarCrops from './SimilarCrops.svelte'
  import { graphemeChar } from '../lib/identity.js'
  import ProductionBadge, { productionLabel } from './ProductionBadge.svelte'
  import SourceCredit from './SourceCredit.svelte'
  import CropForm from './CropForm.svelte'
  import CropTitle from './CropTitle.svelte'
  import FormChips from './FormChips.svelte'
  import { setForm } from '../lib/cropForms.js'
  import AdvanceSwitch from './AdvanceSwitch.svelte'
  import { useSession } from '../lib/session.svelte.js'
  import ZiLink from './ZiLink.svelte'
  import CopyId from './CopyId.svelte'
  import Cite from './Cite.svelte'
  import CitedCut from './CitedCut.svelte'
  import { cropEntry } from '$worker/citation.ts'
  import FavouriteButton from './FavouriteButton.svelte'
  import { onMount, untrack, tick } from 'svelte'
  import { request } from '../lib/client.js'
  import { readCrop } from '../lib/cropCache.js'
  import { isSingle, offersSuggestions, greetSuggestions } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  import CropReview from './CropReview.svelte'
  import CropContext from './CropContext.svelte'
  // `initial` is the record the server rendered the page with, so the first load needs no request.
  // `changed` hears about a write that keeps the dialog open (a written form), as the crop dialog's does.
  // `preview` is the list's own row for the glyph, drawn while the record loads.
  // `cited` is the cut the page's address cites (`citedCut`), noted over the glyph when it is not the one shown.
  let { id, close, saved, changed = null, previous = null, next = null, position = '', initial = null, preview = null, cited = null } = $props()
  const first = untrack(() => initial)
  const session = useSession()
  // Going on to the next crop disables the focused save button while it loads, which drops its focus;
  // once the crop is ready, focus returns to the button the reader was pressing. The save names the
  // crop it leaves, and the load of another crop arms the return.
  let saveButton = $state(null), refocus = $state(false), leaving = null
  // The button is enabled on the render after the crop is ready, so focus waits for it.
  $effect(() => { if (refocus && loaded && fresh && !busy && saveButton) { refocus = false; tick().then(() => saveButton?.focus({ preventScroll: true })) } })
  const advancing = $derived(session.state.advance && Boolean(next))
  let dialog, data = $state(first), error = $state(''), busy = $state(false)
  let issue = $state(null), noneSelected = $state(false), correction = $state(null)
  // The form the reviewer chose for the glyph, held until the save; null keeps the one it has.
  let form = $state(null)
  let loaded = $state(false), imageFailed = $state(false), suggestionsElement = $state(null)
  let generation = 0, closed = false, submission = null, fresh = $state(Boolean(first))
  const sourceName = $derived(data?.source?.corpus === 'codh-full' ? 'CODH' : data?.source?.corpus || t('corpus.genericName'))
  async function load(target, preloaded = null) {
    const current = ++generation
    // The list's row stands in until the record arrives; nothing can be saved from it.
    data = preloaded ?? (preview?.id === target ? preview : null); fresh = Boolean(preloaded)
    error = ''; form = null; issue = null; correction = null; noneSelected = false
    loaded = false; imageFailed = false; submission = null
    if (leaving && target !== leaving) { refocus = true; leaving = null }
    dialog?.scrollTo({ top: 0 })
    try {
      const result = preloaded ?? await readCrop(target, 'corpus')
      if (!closed && current === generation) { if (result.image !== data?.image) loaded = false; data = result; fresh = true }
    } catch (e) { if (!closed && current === generation) error = e.message }
  }
  // Only the first load, of the crop the page was rendered for, starts from `initial`.
  let preloaded = first
  $effect(() => { const target = id; untrack(() => { load(target, preloaded); preloaded = null }) })
  // Rendered open on the server, reopened as a modal once the script runs.
  onMount(() => { if (dialog.open) dialog.close(); dialog.showModal(); return () => { closed = true; generation++ } })
  // A skip goes where a save would: on to the next crop when the reader goes through the list in a row.
  function skip() { if (!busy) { if (advancing) next(); else close() } }
  // ← and → step through the list, as the arrows in the header do; the crop view keeps its own arrows.
  function stepKey(event) {
    if (event.defaultPrevented || busy || event.metaKey || event.ctrlKey || event.altKey) return
    if (event.target.closest?.('input, textarea, select, .crop-viewport, .character-search')) return
    if ([...document.querySelectorAll('dialog[open]')].at(-1) !== dialog) return
    if (event.key === 'ArrowLeft' && previous) { event.preventDefault(); previous() }
    else if (event.key === 'ArrowRight' && next) { event.preventDefault(); next() }
  }
  async function chooseIssue(value) {
    issue = value; correction = null; noneSelected = false; submission = null
    if (offersSuggestions(value)) { await tick(); greetSuggestions(suggestionsElement, { focus: true }) }
  }
  function choose(value, none = false) { correction = value; noneSelected = none; submission = null }
  async function save(matches = false) {
    if (busy || !data || !fresh || !loaded || imageFailed) return
    const target = id, current = generation
    if (data.identity_status === 'unassigned' && (matches || !issue)) return
    if (matches) { issue = null; correction = null; noneSelected = false; form = null }
    busy = true; error = ''
    // A chosen form is written first, and the review that follows names the revision it left. A wrong
    // character names the glyph's character itself, so a form chosen beside it is not written.
    if (form != null && issue !== 'character') {
      try {
        const formed = await setForm({ ...data, origin: 'corpus' }, form)
        changed?.(target, formed.crop)
        if (closed || current !== generation) return
        data = { ...data, ...formed.crop }; form = null
        // A form that named the glyph's character was a review already; nothing more to say.
        if (formed.reviewed && (matches || !issue)) { leaving = advancing ? target : null; saved(target, formed.crop); busy = false; return }
      } catch (e) { if (!closed && current === generation) error = e.message; busy = false; return }
    }
    const payload = { identity: target, revision: data.revision,
      source_revision: data.source_revision, verdict: issue ? 'wrong' : 'match',
      issue, ...(issue && correction ? isSingle(correction)
        ? { character: correction, issue: 'character' } : { correction } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    try {
      leaving = advancing ? target : null
      const result = await request('/atlas/corpus/reviews', { id: submission.id, ...payload })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
</script>

<svelte:window onkeydown={stepKey} />
<dialog class="character-dialog corpus-dialog" bind:this={dialog} open oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label={t('corpus.dialog.label')}>
  <div class="inspector">
    <header class="inspector-header">{#if data}<CopyId id={data.id}>{#if fresh}<Cite entry={cropEntry(data, 'corpus')} />{/if}</CopyId>{/if}<div class="inspector-navigation">{#if data}<FavouriteButton id={data.id} />{/if}<span>{position}</span><button class="icon-button previous-character" aria-label={t('common.previousCharacter')} disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label={t('common.nextCharacter')} disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label={t('common.closeReviewer')} onclick={close}>×</button></div></header>
    <div class="inspector-notices">{#if cited && !cited.current && cited.id === data?.id}<CitedCut {cited} />{/if}
      {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>{t('character.reload')}</button></div>{/if}</div>
    {#if data}
      <!-- The glyph alone, in a box of one size for every glyph, then the page around it further down. -->
      <div class="inspector-page">
        <div class="inspector-figure">
          {#if data.image && data.proxyable}{#key data.id + ':' + data.revision}<CropContext item={data} detail={data} corpus disabled={busy} onload={() => { loaded = true; imageFailed = false }} onerror={() => imageFailed = true} />{/key}{:else}<span class="figure-note">{t('character.image.unavailable')}</span>{/if}
        </div>
        <div class="credit-beside"><SourceCredit item={data} corpus /></div>
      </div>
      <div class="inspector-right">
        <div class="inspector-production">{#if productionLabel(data)}<ProductionBadge item={data} />{/if}</div>
        <div class="inspector-title">{#if data.identity_status !== 'unassigned'}<CropTitle char={data.written_character ?? data.label} script={data.script} />{:else}<h2 class="unassigned-title">{t('corpus.unassigned')}{#if graphemeChar(data)}<span class="title-grapheme" lang="ja" title={t('chips.grapheme')}>{graphemeChar(data)}</span>{/if}</h2>{/if}{#if data.identity_status !== 'unassigned'}<ZiLink character={data.written_character ?? data.label} />{/if}{#if data.needs_segmentation || ['checked', 'flagged', 'stale'].includes(data.state)}<span class="state-pill" class:flagged={data.state === 'flagged'}>{data.needs_segmentation ? t('corpus.state.needsSplitting') : data.state === 'checked' ? t('corpus.state.checkedHere') : data.state === 'flagged' ? t('state.flagged') : t('corpus.state.sourceChanged')}</span>{/if}</div>
        <!-- What the source itself labelled the glyph, shown when it is not what the crop now reads. -->
        {#if data.needs_segmentation || data.identity_status === 'unassigned' || (data.written_character ?? data.label) !== data.source_label}<p class="corpus-source-label">{#if data.needs_segmentation}<span>{t('corpus.characterCount', { count: data.character_count })} · </span>{/if}{t('corpus.sourceLabel', { source: sourceName })} <b lang="ja">{data.source_label}</b> <ZiLink character={data.source_label} compact />{#if data.identity_status !== 'unassigned' && (data.written_character ?? data.label) !== data.source_label}<span> → <b lang="ja">{data.written_character ?? data.label}</b> · {t('corpus.atlasCorrection')}</span>{/if}</p>{/if}
        {#if data.identity_status === 'unassigned'}
          <div class="assignment-options"><FormChips forms={data.family_members ?? []} chosen={issue === 'character' ? correction : null} disabled={busy || !fresh} label={t('corpus.assign.label')}
            onchoose={char => { chooseIssue('character'); choose(char) }} /></div>
        {/if}
        {#snippet formBar()}{#if data.identity_status !== 'unassigned' && !data.needs_segmentation}<CropForm crop={data} chosen={form} onchoose={value => form = value} disabled={busy || !fresh} />{/if}{/snippet}
        <CropReview forms={formBar} {issue} onissue={chooseIssue} disabled={busy || !fresh} suggested={data.state === 'flagged' ? data.issue : null} onskip={skip}
          targetId={data.id} bind:element={suggestionsElement} {noneSelected} result={{ candidates: data.suggestions }} label={data.label} value={correction} onchoose={choose} />
        <SimilarCrops id={data.id} label={data.label} ready={fresh && loaded} />
      </div>
      <div class="credit-after"><SourceCredit item={data} corpus /></div>
    {:else if !error}<div class="inspector-page"><div class="inspector-figure shimmer"></div></div><div class="inspector-right"><div class="inspector-skeleton"></div></div>{/if}
  </div>
  <footer class="inspector-savebar">
    <AdvanceSwitch disabled={busy} />
    {#if imageFailed}<span role="alert">{t('character.image.unavailable')}</span>{/if}
    <button class="primary save-character" bind:this={saveButton} disabled={busy || !data || !fresh || !loaded || imageFailed || !data.proxyable || ((data.needs_segmentation || data.identity_status === 'unassigned') && !issue)} onclick={() => save()}>{busy ? t('common.saving') : data?.identity_status === 'unassigned' && !issue ? t('corpus.save.chooseCharacterOrIssue') : data?.needs_segmentation && !issue ? t('corpus.save.awaitingSegmentation') : issue ? t(advancing ? 'character.save.issue.next' : 'character.save.issue.close') : form != null ? t(advancing ? 'character.save.changes.next' : 'character.save.changes.close') : t(advancing ? 'character.save.looksRight.next' : 'character.save.looksRight.close')} {#if advancing}<span>→</span>{:else if !issue}<span>✓</span>{/if}</button>
    {#if issue && !data?.needs_segmentation && data?.identity_status !== 'unassigned'}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => save(true)}>{t('character.save.itLooksRight')}</button>{/if}
    <ContributionTerms />
  </footer>
</dialog>

<style>
  .assignment-options{margin:0 0 16px}
  .unassigned-title{font-size:28px}
  .title-grapheme{margin-left:12px;font-size:40px;color:var(--muted)}
  .corpus-source-label{font-size:12px;color:var(--muted);margin:-6px 0 18px}.corpus-source-label b{font-size:17px;color:var(--ink);margin-left:6px}

</style>
