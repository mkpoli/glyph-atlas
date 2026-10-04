<script>
  // Pair or trigram frequencies: runs of crops that follow each other on a line, counted by the text
  // their labels make, most frequent first, each written the way most of its occurrences are: down
  // the page or across it. `runs` is `[{ text, n, vertical }]`, or null while it loads.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { runAddress, ngramWords } from '../lib/ngrams.js'

  // `work` is the work the counts are for; a run opens its occurrences in it.
  let { kind = 'pair', runs = null, failed = false, onretry = () => {}, work = '' } = $props()
  const words = $derived(ngramWords(kind))
</script>

{#if failed}
  <p class="candidate-status" role="alert">{words.failed()} <button type="button" onclick={onretry}>{t('common.tryAgain')}</button></p>
{:else if !runs}
  <p class="candidate-status" role="status">…</p>
{:else if !runs.length}
  <p class="candidate-status">{words.none()}</p>
{:else}
  <ul class="category-options ngram-grid">
    {#each runs as run (run.text)}
      <li><a href={localize(runAddress(run.text, work))} aria-label={`${run.text} ${t('ngram.occurrences', { count: number(run.n) })}`}><span class="ngram-text" class:vertical={run.vertical}><ReferenceGlyph char={run.text} size="md" /></span><small>{number(run.n)}</small></a></li>
    {/each}
  </ul>
{/if}

<style>
  .ngram-grid{list-style:none;margin:0}
  .ngram-grid li{background:var(--surface-subtle)}
  .ngram-grid a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:54px;padding:8px 4px;color:inherit;text-decoration:none}
  .ngram-grid a:hover,.ngram-grid a:focus-visible{background:var(--surface-selected)}
  .ngram-text.vertical{writing-mode:vertical-rl;text-orientation:upright}
</style>
