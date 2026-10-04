<script>
  // A run of characters typed into the search box, offered above the candidates once the site has
  // found it: the run, and how often its characters follow each other on a line. It reads the count
  // when the list shows it and the reader pauses, and shows nothing for a run with no occurrence or a
  // count that could not be read.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { runAddress, runOccurrences } from '../lib/ngrams.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { text } = $props()
  let found = $state(null)
  $effect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => runOccurrences(text, { limit: 1 }, { signal: controller.signal })
      .then(page => { found = page }).catch(() => {}), 220)
    return () => { clearTimeout(timer); controller.abort() }
  })
</script>

{#if found?.total}
  <div class="candidate-row"><a class="candidate run-candidate" href={localize(runAddress(text))}>
    <span class="run-candidate-text"><ReferenceGlyph char={text} size="md" /></span>
    <span class="candidate-body"><span class="candidate-reading">{t('search.run')}</span>
      <span class="candidate-counts">{found.more ? t('run.occurrences.more', { count: found.total }) : t('run.occurrences', { count: found.total })}</span></span>
  </a></div>
{/if}

<style>
  .run-candidate{color:inherit;text-decoration:none}
  .run-candidate:focus-visible{background:var(--surface-quiet);outline:none}
  .run-candidate-text{min-width:0;overflow:hidden;white-space:nowrap}
  .candidate-row:has(.run-candidate){border-bottom:1px solid var(--line);margin-bottom:4px}
</style>
