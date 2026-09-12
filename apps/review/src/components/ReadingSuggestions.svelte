<script>
  import { isSingle } from '../lib/issues.js'
  let { result = null, loading = false, issue, reading, value = null, choose, disabled = false } = $props()
  const candidates = $derived((result?.candidates || []).filter(c => c.text !== reading && (issue === 'merged' ? !isSingle(c.text) : isSingle(c.text))).slice(0, 4))
</script>
{#if issue === 'reading' || issue === 'merged'}
  <div class="reading-suggestions">
    <div class="suggestions-heading"><span>{issue === 'merged' ? 'Possible text' : 'Could it be…'}</span><small>Optional</small></div>
    {#if loading}<span class="suggestions-loading">Reading the crop…</span>
    {:else if candidates.length}<div class="suggestion-options">{#each candidates as candidate}<button type="button" class:chosen={value === candidate.text} disabled={disabled} aria-pressed={value === candidate.text} title={candidate.engine} onclick={() => choose(value === candidate.text ? null : candidate.text)}>{candidate.text}</button>{/each}<button type="button" class="no-suggestion" class:chosen={!value} disabled={disabled} onclick={() => choose(null)}>None of these</button></div>
    {:else}<span class="suggestions-empty">No clear suggestion. You can save the issue.</span>{/if}
  </div>
{/if}
