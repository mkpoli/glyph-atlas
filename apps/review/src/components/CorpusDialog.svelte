<script>
  import ContributionTerms from './ContributionTerms.svelte'
  import SimilarCrops from './SimilarCrops.svelte'
  import { graphemeChar } from '../lib/identity.js'
  import ScriptText from './ScriptText.svelte'
  import ScriptLegend from './ScriptLegend.svelte'
  import ProductionBadge, { productionLabel } from './ProductionBadge.svelte'
  import SourceCredit from './SourceCredit.svelte'
  import WrittenFormField from './WrittenFormField.svelte'
  import AdvanceSwitch from './AdvanceSwitch.svelte'
  import { useSession } from '../lib/session.svelte.js'
  import ZiLink from './ZiLink.svelte'
  import CopyId from './CopyId.svelte'
  import { onMount, untrack, tick } from 'svelte'
  import { request, corpusCharacter } from '../lib/client.js'
  import { isSingle, suggestsReading, greetSuggestions, skipHint } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  import IssuePicker from './IssuePicker.svelte'
  import ReadingSuggestions from './ReadingSuggestions.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import CropContext from './CropContext.svelte'
  // `initial` is the record the server rendered the page with, so the first load needs no request.
  // `changed` hears about a write that keeps the dialog open (a written form), as the crop dialog's does.
  let { id, close, saved, changed = null, previous = null, next = null, position = '', initial = null } = $props()
  const first = untrack(() => initial)
  const session = useSession()
  // Going on to the next crop disables the focused save button while it loads, which drops its focus;
  // once the crop is ready, focus returns to the button the reader was pressing. The save names the
  // crop it leaves, and the load of another crop arms the return.
  let saveButton = $state(null), refocus = $state(false), leaving = null
  // The button is enabled on the render after the crop is ready, so focus waits for it.
  $effect(() => { if (refocus && loaded && !busy && saveButton) { refocus = false; tick().then(() => saveButton?.focus({ preventScroll: true })) } })
  const advancing = $derived(session.state.advance && Boolean(next))
  let dialog, data = $state(first), error = $state(''), busy = $state(false)
  let issue = $state(null), noneSelected = $state(false), correction = $state(null), search = $state('')
  let loaded = $state(false), imageFailed = $state(false), suggestionsElement = $state(null)
  let generation = 0, closed = false, submission = null
  const sourceName = $derived(data?.source?.corpus === 'codh-full' ? 'CODH' : data?.source?.corpus || t('corpus.genericName'))
  async function load(target, preloaded = null) {
    const current = ++generation
    data = preloaded; error = ''; issue = null; correction = null; noneSelected = false; search = ''
    loaded = false; imageFailed = false; submission = null
    if (leaving && target !== leaving) { refocus = true; leaving = null }
    dialog?.scrollTo({ top: 0 })
    try {
      const result = preloaded ?? await corpusCharacter(target)
      if (!closed && current === generation) data = result
    } catch (e) { if (!closed && current === generation) error = e.message }
  }
  // Only the first load, of the crop the page was rendered for, starts from `initial`.
  let preloaded = first
  $effect(() => { const target = id; untrack(() => { load(target, preloaded); preloaded = null }) })
  // Rendered open on the server, reopened as a modal once the script runs.
  onMount(() => { if (dialog.open) dialog.close(); dialog.showModal(); return () => { closed = true; generation++ } })
  // A skip goes where a save would: on to the next crop when the reader goes through the list in a row.
  function skip() { if (!busy) { if (advancing) next(); else close() } }
  async function chooseIssue(value) {
    issue = value; correction = null; noneSelected = false; submission = null; search = ''
    if (suggestsReading(value)) { await tick(); greetSuggestions(suggestionsElement, { focus: true }) }
  }
  function choose(value, none = false) { correction = value; noneSelected = none; submission = null }
  async function save(matches = false) {
    if (busy || !data || !loaded || imageFailed) return
    const target = id, current = generation
    if (data.identity_status === 'unassigned' && (matches || !issue)) return
    if (matches) { issue = null; correction = null; noneSelected = false }
    const payload = { identity: target, revision: data.revision,
      source_revision: data.source_revision, verdict: issue ? 'wrong' : 'match',
      issue, ...(issue && correction ? isSingle(correction)
        ? { character: correction, issue: 'character' } : { correction } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    busy = true; error = ''
    try {
      leaving = advancing ? target : null
      const result = await request('/atlas/corpus/reviews', { id: submission.id, ...payload })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
  // A written form saved for one glyph may come back after the dialog has moved to another; the
  // glyph is marked as changed either way, and the record on screen is replaced only when it is that one.
  function formed(result) {
    changed?.(result.id, result)
    if (!closed && result.id === data?.id) data = result
  }
</script>

<dialog class="character-dialog corpus-dialog" bind:this={dialog} open oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label={t('corpus.dialog.label')}>
  <div class="inspector">
    <header class="inspector-header"><div class="inspector-navigation"><span>{position}</span><button class="icon-button previous-character" aria-label={t('common.previousCharacter')} disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label={t('common.nextCharacter')} disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label={t('common.closeReviewer')} onclick={close}>×</button></div></header>
    {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>{t('character.reload')}</button></div>{/if}
    {#if data}
      <div class="inspector-production">{#if productionLabel(data)}<ProductionBadge item={data} />{/if}{#if data.identity_status !== 'unassigned' && !data.needs_segmentation}<WrittenFormField item={data} corpus disabled={busy} working={value => busy = value} saved={formed} />{/if}</div>
      <div class="inspector-title"><h2 class:unassigned-title={data.identity_status === 'unassigned'}>{#if data.identity_status === 'unassigned'}{t('corpus.unassigned')}{#if graphemeChar(data)}<span class="title-grapheme" lang="ja" title={t('chips.grapheme')}>{graphemeChar(data)}</span>{/if}{:else}<ReferenceGlyph char={data.written_character ?? data.label} code_point={data.code_point} size="lg" />{/if}</h2>{#if data.identity_status !== 'unassigned'}<ZiLink character={data.written_character ?? data.label} />{/if}{#if data.needs_segmentation || ['checked', 'flagged', 'stale'].includes(data.state)}<span class="state-pill" class:flagged={data.state === 'flagged'}>{data.needs_segmentation ? t('corpus.state.needsSplitting') : data.state === 'checked' ? t('corpus.state.checkedHere') : data.state === 'flagged' ? t('state.flagged') : t('corpus.state.sourceChanged')}</span>{/if}</div><CopyId id={data.id} />
      <!-- What the source itself labelled the glyph, shown when it is not what the crop now reads. -->
      {#if data.needs_segmentation || data.label !== data.source_label}<p class="corpus-source-label">{#if data.needs_segmentation}<span>{t('corpus.characterCount', { count: data.character_count })} · </span>{/if}{t('corpus.sourceLabel', { source: sourceName })} <b lang="ja">{data.source_label}</b> <ZiLink character={data.source_label} compact />{#if data.identity_status !== 'unassigned' && data.label !== data.source_label}<span> → <b lang="ja">{data.label}</b> · {t('corpus.atlasCorrection')}</span>{/if}</p>{/if}
      <div class="inspector-figure">
        {#if data.image && data.proxyable}
          {#key data.id + ':' + data.revision}<CropContext item={data} detail={data} corpus disabled={busy} onload={() => { loaded = true; imageFailed = false }} onerror={() => imageFailed = true} />{/key}
        {:else}<span>{t('character.image.unavailable')}</span>{/if}
        <div class="credit-beside"><SourceCredit item={data} corpus /></div>
      </div>
      {#if data.identity_status === 'unassigned'}
        <div class="assignment-options" aria-label={t('corpus.assign.label')}>
          {#each data.family_members ?? [] as member}
            {@const char = typeof member === 'string' ? member : member.char}
            <span><button class:chosen={correction === char} disabled={busy} onclick={() => { chooseIssue('character'); choose(char) }}><ScriptText text={char} script={typeof member === 'object' ? member.script : ''} /></button><ZiLink character={char} compact /></span>
          {/each}
        </div>
        <ScriptLegend />
      {/if}
      <IssuePicker value={issue} choose={chooseIssue} disabled={busy} suggested={data.state === 'flagged' ? data.issue : null} />
      <!-- Any other character is chosen beside the suggestions, in the same row as "None of these"; a crop of
           joined characters takes what it holds as typed text. -->
      {#snippet other()}<div class="corpus-pick"><span>{t('corpus.chooseAnother')}</span><CharacterSearch bind:value={search} label={t('corpus.correctCharacter.label')} placeholder={t('suggestions.type.placeholder')} onselect={item => choose(item.char)} /></div>{/snippet}
      <ReadingSuggestions typing={issue === 'merged'} targetId={data.id} bind:element={suggestionsElement} {noneSelected} result={{ candidates: data.suggestions }} {issue} reading={data.label} value={correction} disabled={busy} {choose} other={['reading', 'character'].includes(issue) ? other : null} />
      {#if ['reading', 'character'].includes(issue) && correction}<p class="corpus-choice" role="status"><span lang="ja">{data.label}</span> → <b lang="ja">{correction}</b><button disabled={busy} onclick={() => choose(null)}>{t('common.clear')}</button></p>{/if}
      <SimilarCrops id={data.id} label={data.label} />
      <div class="credit-after"><SourceCredit item={data} corpus /></div>
    {:else if !error}<div class="inspector-skeleton"></div>{/if}
  </div>
  <footer class="inspector-savebar">
    {#if position}<AdvanceSwitch disabled={busy} />{/if}
    {#if imageFailed}<span role="alert">{t('character.image.unavailable')}</span>{/if}
    <button class="primary save-character" bind:this={saveButton} disabled={busy || !data || !loaded || imageFailed || !data.proxyable || ((data.needs_segmentation || data.identity_status === 'unassigned') && !issue)} onclick={() => save()}>{busy ? t('common.saving') : data?.identity_status === 'unassigned' && !issue ? t('corpus.save.chooseCharacterOrIssue') : data?.needs_segmentation && !issue ? t('corpus.save.awaitingSegmentation') : issue ? t(advancing ? 'character.save.issue.next' : 'character.save.issue.close') : t(advancing ? 'character.save.looksRight.next' : 'character.save.looksRight.close')} {#if advancing}<span>→</span>{:else if !issue}<span>✓</span>{/if}</button>
    {#if issue && !data?.needs_segmentation && data?.identity_status !== 'unassigned'}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => save(true)}>{t('character.save.itLooksRight')}</button>{/if}
    <button class="skip-character" disabled={busy} onclick={skip} title={skipHint()}>{t(advancing ? 'common.skip.next' : 'common.skip.close')}</button>
    <ContributionTerms />
  </footer>
</dialog>

<style>
  .assignment-options{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}.assignment-options>span{display:flex;flex-direction:column;align-items:center;gap:4px}.assignment-options button{display:flex;align-items:center;justify-content:center;height:62px;min-width:66px;padding:0 18px;font-size:28px;line-height:1}.assignment-options button.chosen{border-color:var(--accent);background:var(--accent-light)}
  .unassigned-title{font-size:28px}
  .title-grapheme{margin-left:12px;font-size:40px;color:var(--muted)}
  .corpus-source-label{font-size:12px;color:var(--muted);margin:-6px 0 18px}.corpus-source-label b{font-size:17px;color:var(--ink);margin-left:6px}
  .corpus-pick{display:flex;flex-direction:column;gap:5px;font-size:11px;color:var(--muted);flex:1 1 16em;min-width:0}.corpus-pick :global(.character-search){width:100%;min-width:0}.corpus-choice{display:flex;align-items:center;gap:12px;margin-top:12px}.corpus-choice b{font-size:24px}.corpus-choice button{margin-left:auto;padding:5px 10px}
</style>
