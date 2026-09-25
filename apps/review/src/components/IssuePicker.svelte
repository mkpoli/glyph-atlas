<script>
  import { issues, issueTitle, issueHint, skipLabel, skipHint } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'
  // `skip`, when given, adds a Skip card: not a problem, and chosen only while the crop is skipped.
  let { value = null, choose, disabled = false, suggested = null, compact = false, skip = null, skipped = false } = $props()
</script>
<div class="issue-picker" class:compact aria-label={t('issue.picker.label')}>
  {#each issues as issue}
    <button type="button" class="issue-card" class:chosen={value === issue.id} data-issue={issue.id} disabled={disabled} aria-pressed={value === issue.id} onclick={() => choose(issue.id)}>
      <span class="issue-example" class:joined={issue.id === 'merged'} class:cropped={issue.id === 'crop'} aria-hidden="true">
        {#if issue.id === 'reading'}<span class="example-old">ア</span><svg viewBox="0 0 24 24"><path d="M4 12h16m-6-6 6 6-6 6"/></svg><span>カ</span>
        {:else if issue.id === 'merged'}<span>ア</span><i></i><span>カ</span>
        {:else if issue.id === 'crop'}<span class="crop-window">ア</span><svg viewBox="0 0 24 24"><path d="M7 2v15h15M2 7h15v15"/></svg>
        {:else if issue.id === 'blank'}<svg viewBox="0 0 48 40"><rect x="5" y="3" width="38" height="34" rx="4" stroke-dasharray="3 4"/><path d="m17 24 2 1m10-11 1 2m-5 13 2-1"/></svg>{/if}
      </span>
      <span class="issue-name">{issueTitle(issue.id)}</span><span class="issue-hint">{issueHint(issue.id)}</span>
      {#if suggested === issue.id}<span class="suggested-tag">{t('issue.suggested')}</span>{/if}
    </button>
  {/each}
  {#if skip}
    <button type="button" class="issue-card skip-card" class:chosen={skipped} data-issue="skip" {disabled} aria-pressed={skipped} onclick={skip}>
      <span class="issue-example" aria-hidden="true">?</span>
      <span class="issue-name">{skipLabel()}</span><span class="issue-hint">{skipHint()}</span>
    </button>
  {/if}
</div>
