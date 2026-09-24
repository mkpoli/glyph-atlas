<script>
  import ScriptText from './ScriptText.svelte'
  import ScriptLegend from './ScriptLegend.svelte'
  import ZiLink from './ZiLink.svelte'
  import { isSingle, suggestsReading } from '../lib/issues.js'
  let { result = null, loading = false, contextResult = undefined, contextLoading = false,
    targetId = '', issue, reading, value = null, noneSelected = false, choose, disabled = false,
    element = $bindable(null) } = $props()
  let candidates = $state([]), scripts = $state({})
  let order = [], previousKey = null
  $effect(() => {
    const key = targetId + ':' + (issue === 'merged' ? 'multiple' : 'single')
    const incoming = [...(result?.candidates || []), ...(contextResult?.candidates || [])]
      .filter(c => c.text && c.text !== reading && (issue === 'merged' ? !isSingle(c.text) : isSingle(c.text)))
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
  <div class="reading-suggestions" bind:this={element} tabindex="-1" aria-label="Suggestions">
    <div class="suggestions-heading">Suggestions</div>
    {#if candidates.length}
      <div class="suggestion-options">
        {#each candidates as text (text)}
          <span class="suggestion-choice"><button type="button" class:chosen={value === text} {disabled} aria-pressed={value === text}
            onclick={() => choose(value === text ? null : text, false)}><ScriptText {text} script={scripts[text]} /></button><ZiLink character={text} compact /></span>
        {/each}
      </div>
      <ScriptLegend />
    {/if}
    {#if loading || contextLoading}<p class="suggestions-loading" role="status">Finding suggestions…</p>
    {:else if !candidates.length}<p class="suggestions-empty">No clear suggestion.</p>{/if}
    <button type="button" class="no-suggestion" class:chosen={noneSelected} aria-pressed={noneSelected} {disabled} onclick={() => choose(null, true)}>{#if noneSelected}<span aria-hidden="true">✓ </span>{/if}None of these / not sure</button>
  </div>
{/if}

<style>
  .suggestion-choice{display:flex;flex-direction:column;align-items:center;gap:4px}
  .suggestion-options{display:flex;flex-wrap:wrap;gap:8px}
  .reading-suggestions :global(.script-legend){margin-top:12px}
  .suggestion-options button{font-size:24px;padding:10px 14px;min-width:48px;max-width:100%;overflow-wrap:anywhere}
  .suggestions-loading,.suggestions-empty{margin-top:10px}
  .no-suggestion{margin-top:16px;font-size:12px;padding:11px 14px;color:var(--ink);border:1px solid var(--line);background:white;border-radius:7px;cursor:pointer}
  .no-suggestion.chosen{background:var(--accent-light);border-color:var(--accent);color:var(--accent)}
</style>
