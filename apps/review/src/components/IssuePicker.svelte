<script>
  import { issues } from '../lib/issues.js'
  let { value = null, choose, disabled = false, suggested = null, compact = false } = $props()
</script>
<div class="issue-picker" class:compact aria-label="Error type">
  {#each issues as issue}
    <button type="button" class="issue-card" class:chosen={value === issue.id} data-issue={issue.id} disabled={disabled} aria-pressed={value === issue.id} onclick={() => choose(issue.id)}>
      <span class="issue-example" class:joined={issue.id === 'merged'} class:cropped={issue.id === 'crop'} class:faint={issue.id === 'unclear'} aria-hidden="true">
        {#if issue.id === 'reading'}<span class="example-old">ア</span><svg viewBox="0 0 24 24"><path d="M4 12h16m-6-6 6 6-6 6"/></svg><span>カ</span>
        {:else if issue.id === 'merged'}<span>ア</span><i></i><span>カ</span>
        {:else if issue.id === 'crop'}<span class="crop-window">ア</span><svg viewBox="0 0 24 24"><path d="M7 2v15h15M2 7h15v15"/></svg>
        {:else if issue.id === 'blank'}<svg viewBox="0 0 48 40"><rect x="5" y="3" width="38" height="34" rx="4" stroke-dasharray="3 4"/><path d="m17 24 2 1m10-11 1 2m-5 13 2-1"/></svg>
        {:else}<span class="unclear-ink">あ</span><strong>?</strong>{/if}
      </span>
      <span class="issue-name">{issue.title}</span><span class="issue-hint">{issue.hint}</span>
      {#if suggested === issue.id}<span class="suggested-tag">Suggested</span>{/if}
    </button>
  {/each}
</div>
