<script>
  // Two-character frequencies: crops that follow each other on a line, counted by the text their
  // labels make, most frequent first. `pairs` is `[{ text, n }]`, or null while it loads.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { pairAddress } from '../lib/pairs.js'

  // `work` is the work the counts are for; a pair opens its occurrences in it.
  let { pairs = null, failed = false, onretry = () => {}, work = '' } = $props()
</script>

{#if failed}
  <p class="candidate-status" role="alert">{t('explore.pairs.failed')} <button type="button" onclick={onretry}>{t('common.tryAgain')}</button></p>
{:else if !pairs}
  <p class="candidate-status" role="status">…</p>
{:else if !pairs.length}
  <p class="candidate-status">{t('explore.pairs.none')}</p>
{:else}
  <ul class="category-options pair-grid">
    {#each pairs as pair (pair.text)}
      <li><a href={localize(pairAddress(pair.text, work))} aria-label={`${pair.text} ${t('pair.occurrences', { count: number(pair.n) })}`}><ReferenceGlyph char={pair.text} size="md" /><small>{number(pair.n)}</small></a></li>
    {/each}
  </ul>
{/if}

<style>
  .pair-grid{list-style:none;margin:0}
  .pair-grid li{background:var(--surface-subtle)}
  .pair-grid a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:54px;padding:8px 4px;color:inherit;text-decoration:none}
  .pair-grid a:hover,.pair-grid a:focus-visible{background:var(--surface-selected)}
</style>
