<script>
  import ScriptText from './ScriptText.svelte'
  import ScriptLegend from './ScriptLegend.svelte'
  import ProductionBadge from './ProductionBadge.svelte'
  import ZiLink from './ZiLink.svelte'
  import { onMount, untrack, tick } from 'svelte'
  import { request, corpusCharacter } from '../lib/client.js'
  import { isSingle, suggestsReading, greetSuggestions, skipHint } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  import IssuePicker from './IssuePicker.svelte'
  import ReadingSuggestions from './ReadingSuggestions.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import CropContext from './CropContext.svelte'
  let { id, clientId, close, saved, previous = null, next = null, position = '' } = $props()
  let dialog, data = $state(null), error = $state(''), busy = $state(false)
  let issue = $state(null), noneSelected = $state(false), correction = $state(null), note = $state(''), search = $state('')
  let loaded = $state(false), imageFailed = $state(false), suggestionsElement = $state(null)
  let generation = 0, closed = false, submission = null
  const sourceName = $derived(data?.source?.corpus === 'codh-full' ? 'CODH' : data?.source?.corpus || t('corpus.genericName'))
  async function load(target) {
    const current = ++generation
    data = null; error = ''; issue = null; correction = null; noneSelected = false; note = ''; search = ''
    loaded = false; imageFailed = false; submission = null
    dialog?.scrollTo({ top: 0 })
    try {
      const result = await corpusCharacter(target)
      if (!closed && current === generation) data = result
    } catch (e) { if (!closed && current === generation) error = e.message }
  }
  $effect(() => { const target = id; untrack(() => load(target)) })
  onMount(() => { dialog.showModal(); return () => { closed = true; generation++ } })
  function skip() { if (!busy) { if (next) next(); else close() } }
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
    const payload = { identity: target, client_id: clientId, revision: data.revision,
      source_revision: data.source_revision, verdict: issue ? 'wrong' : 'match',
      issue, note, ...(issue && correction ? isSingle(correction)
        ? { character: correction, issue: 'character' } : { correction } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    busy = true; error = ''
    try {
      const result = await request('/atlas/corpus/reviews', { id: submission.id, ...payload })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
</script>

<dialog class="character-dialog corpus-dialog" bind:this={dialog} oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label={t('corpus.dialog.label')}>
  <div class="inspector">
    <header class="inspector-header"><span class="overline">{data?.needs_segmentation ? t('corpus.overline.group') : t('character.overline')}</span><div class="inspector-navigation"><span>{position}</span><button class="icon-button previous-character" aria-label={t('common.previousCharacter')} disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label={t('common.nextCharacter')} disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label={t('common.closeReviewer')} onclick={close}>×</button></div></header>
    {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>{t('character.reload')}</button></div>{/if}
    {#if data}
      <div class="inspector-production"><ProductionBadge item={data} /></div>
      <div class="inspector-title"><h2 class:unassigned-title={data.identity_status === 'unassigned'}>{#if data.identity_status === 'unassigned'}{t('corpus.unassigned')}{:else}<ReferenceGlyph char={data.written_character ?? data.label} code_point={data.code_point} size="lg" />{/if}</h2>{#if data.identity_status !== 'unassigned'}<ZiLink character={data.written_character ?? data.label} />{/if}<span class="state-pill" class:flagged={data.state === 'flagged'}>{data.needs_segmentation ? t('corpus.state.needsSplitting') : data.state === 'checked' ? t('corpus.state.checkedHere') : data.state === 'flagged' ? t('state.flagged') : data.state === 'stale' ? t('corpus.state.sourceChanged') : t('state.unreviewed')}</span></div>
      <p class="corpus-source-label">{#if data.needs_segmentation}<span>{t('corpus.characterCount', { count: data.character_count })} · </span>{/if}{t('corpus.sourceLabel', { source: sourceName })} <b>{data.source_label}</b> <ZiLink character={data.source_label} compact />{#if data.identity_status !== 'unassigned' && data.label !== data.source_label}<span> → <b>{data.label}</b> · {t('corpus.atlasCorrection')}</span>{/if}</p>
      <div class="inspector-figure">
        {#if data.image && data.proxyable}
          {#key data.id + ':' + data.revision}<CropContext item={data} detail={data} corpus disabled={busy} onload={() => { loaded = true; imageFailed = false }} onerror={() => imageFailed = true} />{/key}
        {:else}<span>{t('character.image.unavailable')}</span>{/if}
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
      <div class="inspector-question"><strong>{t('character.question.whatsWrong')}</strong><span>{t('character.question.chooseOne')}</span></div>
      <IssuePicker value={issue} choose={chooseIssue} disabled={busy} suggested={data.state === 'flagged' ? data.issue : null} />
      <ReadingSuggestions targetId={data.id} bind:element={suggestionsElement} {noneSelected} result={{ candidates: data.suggestions }} {issue} reading={data.label} value={correction} disabled={busy} {choose} />
      {#if ['reading', 'character'].includes(issue)}
        <details class="corpus-pick"><summary>{t('corpus.chooseAnother')}</summary><CharacterSearch bind:value={search} label={t('corpus.correctCharacter.label')} placeholder={t('corpus.correctCharacter.placeholder')} onselect={item => choose(item.char)} />{#if correction}<p class="corpus-choice" role="status">{data.label} → <b>{correction}</b><button disabled={busy} onclick={() => choose(null)}>{t('common.clear')}</button></p>{/if}</details>
      {/if}
      <details class="advanced-edit"><summary>{t('corpus.addNote')}</summary><textarea aria-label={t('character.note.aria')} bind:value={note} rows="2" maxlength="2000" placeholder={t('character.note.placeholder')} disabled={busy}></textarea></details>
      <div class="corpus-credit"><span>{data.source?.title}</span><small>{[data.source?.holder, data.licence].filter(Boolean).join(' · ')}</small>{#if /^https?:\/\//i.test(data.record_url ?? '')}<a href={data.record_url} target="_blank" rel="noreferrer">{t('corpus.sourceRecord')}</a>{/if}</div>
    {:else if !error}<div class="inspector-skeleton"></div>{/if}
  </div>
  <footer class="inspector-savebar">
    {#if imageFailed}<span role="alert">{t('character.image.unavailable')}</span>{/if}
    <button class="primary save-character" disabled={busy || !data || !loaded || imageFailed || !data.proxyable || ((data.needs_segmentation || data.identity_status === 'unassigned') && !issue)} onclick={() => save()}>{busy ? t('common.saving') : data?.identity_status === 'unassigned' && !issue ? t('corpus.save.chooseCharacterOrIssue') : data?.needs_segmentation && !issue ? t('corpus.save.awaitingSegmentation') : issue ? (next ? t('character.save.issueNext') : t('character.save.issue')) : (next ? t('character.save.looksRightNext') : t('character.save.looksRight'))} <span>{issue ? '→' : '✓'}</span></button>
    {#if issue && !data?.needs_segmentation && data?.identity_status !== 'unassigned'}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => save(true)}>{t('character.save.itLooksRight')}</button>{/if}
    <button class="skip-character" disabled={busy} onclick={skip} title={skipHint()}>{t('common.skip.arrow')}</button>
  </footer>
</dialog>

<style>
  .assignment-options{display:flex;gap:12px;margin:18px 0}.assignment-options>span{display:flex;flex-direction:column;align-items:center;gap:4px}.assignment-options button{font-size:28px;padding:10px 18px}.assignment-options button.chosen{border-color:var(--accent);background:var(--accent-light)}
  .unassigned-title{font-size:28px}
  .corpus-source-label{font-size:12px;color:var(--muted);margin:-6px 0 18px}.corpus-source-label b{font-size:17px;color:var(--ink);margin-left:6px}
  .corpus-pick{margin-top:18px;font-size:12px}.corpus-pick summary{cursor:pointer;padding:8px 0}.corpus-pick :global(.character-search){margin-top:8px;width:100%}.corpus-choice{display:flex;align-items:center;gap:12px;margin-top:12px}.corpus-choice b{font-size:24px}.corpus-choice button{margin-left:auto;padding:5px 10px}
  .corpus-credit{display:flex;flex-direction:column;gap:6px;margin-top:24px;color:var(--muted);font-size:12px}.corpus-credit a{align-self:flex-start;text-decoration:underline;text-underline-offset:3px;font-size:11px}
</style>
