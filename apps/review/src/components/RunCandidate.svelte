<script>
  // A run of characters typed into the search box, offered above the candidates once the site has
  // found it: the run, any iteration-mark equivalents (踊り字), and how often their characters follow
  // each other on a line. It reads the counts when the list shows it and the reader pauses, and shows
  // nothing when neither the run nor an equivalent has an occurrence.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { runAddress, runOccurrences, runRelated } from '../lib/ngrams.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { text } = $props()
  let found = $state(null)
  let associated = $state([])
  $effect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      runOccurrences(text, { limit: 1 }, { signal: controller.signal })
        .then(page => { found = page }).catch(() => {})
      runRelated(text, { signal: controller.signal })
        .then(page => { associated = (page?.iteration ?? []).filter(item => item.text !== text).slice(0, 4) }).catch(() => {})
    }, 220)
    return () => { clearTimeout(timer); controller.abort() }
  })
</script>

{#if found?.total || associated.length}
  <div class="run-candidates">
    {#if found?.total}
      <div class="candidate-row"><a class="candidate run-candidate" href={localize(runAddress(text))}>
        <span class="run-candidate-text"><ReferenceGlyph char={text} size="md" /></span>
        <span class="candidate-body"><span class="candidate-reading">{t('search.run')}</span>
          <span class="candidate-counts">{found.more ? t('run.occurrences.more', { count: found.total }) : t('run.occurrences', { count: found.total })}</span></span>
      </a></div>
    {/if}
    {#each associated as item (item.text)}
      <div class="candidate-row"><a class="candidate run-candidate" href={localize(runAddress(item.text))}>
        <span class="run-candidate-text"><ReferenceGlyph char={item.text} size="md" /></span>
        <span class="candidate-body"><span class="candidate-reading">{t('run.near.iteration')}</span>
          <span class="candidate-counts">{item.more ? t('run.occurrences.more', { count: item.n }) : t('run.occurrences', { count: item.n })}</span></span>
      </a></div>
    {/each}
  </div>
{/if}

<style>
  .run-candidates{border-bottom:1px solid var(--line);margin-bottom:4px}
  .run-candidate{color:inherit;text-decoration:none}
  .run-candidate:focus-visible{background:var(--surface-quiet);outline:none}
  .run-candidate-text{min-width:0;overflow:hidden;white-space:nowrap}
</style>
