<script>
  import ScriptText from './ScriptText.svelte'
  import ScriptLegend from './ScriptLegend.svelte'
  import ZiLink from './ZiLink.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import { isSingle, suggestsReading } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  // What a wrong or joined crop holds: the characters the recognisers suggest, and any other picked
  // from the character search. Nothing typed is saved as it stands; a joined crop's characters are
  // picked one after another.
  let { result = null, loading = false, contextResult = undefined, contextLoading = false,
    targetId = '', issue, reading, value = null, noneSelected = false, choose, disabled = false,
    element = $bindable(null) } = $props()
  let candidates = $state([]), scripts = $state({}), query = $state('')
  const joined = $derived(issue === 'merged')
  // A choice that is none of the suggestions was picked from the search.
  const picked = $derived(value && !candidates.includes(value) ? value : '')
  function pickOther(item) {
    query = ''
    choose(joined ? picked + item.char : item.char, false)
  }
  let order = [], previousKey = null
  $effect(() => {
    const key = targetId + ':' + (joined ? 'multiple' : 'single')
    const incoming = [...(result?.candidates || []), ...(contextResult?.candidates || [])]
      .filter(c => c.text && c.text !== reading && (joined ? !isSingle(c.text) : isSingle(c.text)))
    const texts = new Set(incoming.map(c => c.text))
    scripts = Object.fromEntries(incoming.filter(c => c.script).map(c => [c.text, c.script]))
    if (key !== previousKey) order = []
    previousKey = key
    // Keep buttons in their arrival order while independent requests finish.
    order = [...order.filter(text => texts.has(text)), ...[...texts].filter(text => !order.includes(text))]
    // A fast context response may fill all six slots before image OCR finishes.
    // Keep their positions, while making a newly recognized printed mark visible.
    const visible = order.slice(0, 6)
    const symbols = incoming.filter(c => c.basis === 'symbol-parts').map(c => c.text)
    candidates = [...visible, ...symbols.filter(text => !visible.includes(text))]
  })
</script>

{#if suggestsReading(issue)}
  <div class="reading-suggestions" bind:this={element} tabindex="-1" aria-label={t('suggestions.label')}>
    <div class="suggestions-heading">{t('suggestions.heading')}</div>
    {#if candidates.length || picked}
      <div class="suggestion-options">
        {#each candidates as text (text)}
          <span class="suggestion-choice"><button type="button" class:chosen={value === text} {disabled} aria-pressed={value === text}
            onclick={() => choose(value === text ? null : text, false)}><ScriptText {text} script={scripts[text]} /></button><ZiLink character={text} compact /></span>
        {/each}
        {#if picked}
          <span class="suggestion-choice picked"><button type="button" class="chosen" {disabled} aria-pressed="true" title={t('common.clear')}
            onclick={() => choose(null, false)}><ScriptText text={picked} /><span class="picked-clear" aria-hidden="true">×</span></button>{#if isSingle(picked)}<ZiLink character={picked} compact />{/if}</span>
        {/if}
      </div>
      <ScriptLegend />
    {/if}
    {#if loading || contextLoading}<p class="suggestions-loading" role="status">{t('suggestions.finding')}</p>
    {:else if !candidates.length && !picked}<p class="suggestions-empty">{t('suggestions.none')}</p>{/if}
    <div class="suggestion-end">
      <div class="suggestion-pick"><CharacterSearch compact codePoints bind:value={query} label={joined && picked ? t('suggestions.pick.next') : t('corpus.chooseAnother')}
        placeholder={joined && picked ? t('suggestions.pick.next') : t('search.placeholder')} onselect={pickOther} /></div>
      <button type="button" class="no-suggestion" class:chosen={noneSelected} aria-pressed={noneSelected} {disabled} onclick={() => choose(null, true)}>{#if noneSelected}<span aria-hidden="true">✓ </span>{/if}{t('suggestions.noneOfThese')}<kbd aria-hidden="true">N</kbd></button>
    </div>
  </div>
{/if}

<style>
  .suggestion-choice{display:flex;flex-direction:column;align-items:center;gap:4px}
  .suggestion-options{display:flex;flex-wrap:wrap;gap:8px}
  .reading-suggestions :global(.script-legend){margin-top:12px}
  .suggestion-options button{font-size:24px;padding:10px 14px;min-width:48px;max-width:100%;overflow-wrap:anywhere}
  .picked button{display:inline-flex;align-items:center;gap:8px}
  .picked-clear{font-size:14px;color:var(--muted)}
  .suggestions-loading,.suggestions-empty{margin-top:10px}
  .suggestion-end{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:16px}
  .suggestion-pick{flex:1 1 14em;min-width:0}
  .suggestion-pick :global(.character-search){width:100%;min-width:0}
  .no-suggestion{display:inline-flex;align-items:center;gap:8px;font-size:12px;padding:11px 14px;color:var(--ink);border:1px solid var(--line);background:var(--surface);border-radius:7px;cursor:pointer}
  .no-suggestion kbd{font-family:"GenZui Sans",ui-monospace,monospace;font-size:9px;color:var(--muted)}
  .no-suggestion.chosen{background:var(--accent-light);border-color:var(--accent);color:var(--accent)}
  @media(hover:none){.no-suggestion kbd{display:none}}
</style>
