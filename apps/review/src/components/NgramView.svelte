<script>
  // Every occurrence of a pair or trigram: the crops that follow each other on a line, shown as they
  // sit on the page (`NgramImage`), with the book and page they come from. The run's own text is written
  // the way most of its occurrences are: down the page or across it. Pages arrive as the reader nears the
  // end, and while more are to come the grid shows whole rows only.
  import { untrack } from 'svelte'
  import NgramImage from './NgramImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import SiteLinks from './SiteLinks.svelte'
  import { ngramOccurrences, ngramWords } from '../lib/ngrams.js'
  import { tileDate } from '../lib/dating.js'
  import { collectionAddress } from '../lib/gallery.js'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { kind = 'pair', text, work = '', first = null, inspect } = $props()
  const words = $derived(ngramWords(kind))
  const opened = untrack(() => first)
  let items = $state(opened?.items ?? []), total = $state(opened?.total ?? null), offset = $state(opened?.next_offset ?? 0), vertical = $state(opened?.vertical ?? true)
  let loading = $state(!opened), error = $state(''), requestId = 0

  async function load(append = false) {
    const id = ++requestId
    loading = true; error = ''
    try {
      const page = await ngramOccurrences(kind, text, { work, offset: append ? offset : 0 })
      if (id !== requestId) return
      items = append ? [...items, ...page.items] : page.items
      total = page.total; offset = page.next_offset; vertical = page.vertical
    } catch (e) { if (id === requestId) error = e.message }
    finally { if (id === requestId) loading = false }
  }
  $effect(() => { if (!opened) untrack(() => load()) })

  const hasMore = $derived(total !== null && items.length < total && offset < 2000)
  // The crops the inspector steps through, in the order they are shown.
  const crops = $derived(items.flatMap(o => o.crops))

  let grid = $state(), sentinel = $state(), columns = $state(0), nearEnd = $state(false)
  const shown = $derived(hasMore && columns && items.length >= columns ? items.slice(0, Math.floor(items.length / columns) * columns) : items)
  $effect(() => { if (nearEnd && hasMore && !loading && !error) untrack(() => load(true)) })
  $effect(() => {
    if (!grid) return
    const measure = () => { columns = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(grid)
    return () => observer.disconnect()
  })
  $effect(() => {
    if (!sentinel) return
    const observer = new IntersectionObserver(([entry]) => { nearEnd = entry.isIntersecting }, { rootMargin: '0px 0px 1200px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  })
  const where = crop => [crop.source, crop.page_number ? t('tile.page', { page: crop.page_number }) : null, tileDate(crop) || null].filter(Boolean).join(' · ')
</script>

<section class="explore ngram-view">
  <div class="page-status">
    <h1 class="visually-hidden">{text} · {words.name()}</h1>
    <SiteLinks />
  </div>
  <header class="ngram-heading">
    <a class="quiet-link" href={localize(collectionAddress({ work }))}>← {words.name()}</a>
    <p><b class:vertical><ScriptText {text} /></b>{#if total !== null}<span>{t('ngram.occurrences', { count: number(total) })}</span>{/if}</p>
  </header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load(items.length > 0)}>{t('common.tryAgain')}</button></div>{/if}
  <div class="ngram-grid" bind:this={grid} aria-busy={loading}>
    {#if loading && !items.length}{#each Array(12) as _}<div class="glyph-skeleton"></div>{/each}{/if}
    {#each shown as occurrence (occurrence.crops[0].id)}
      <article class="ngram-occurrence">
        <div class="ngram-page"><NgramImage crops={occurrence.crops} page={occurrence.page} vertical={occurrence.vertical} oninspect={id => inspect(id, null, crops)} /></div>
        <p class="ngram-where">{where(occurrence.crops[0])}</p>
      </article>
    {/each}
  </div>
  {#if !loading && !error && total === 0}<p class="ngram-empty">{words.empty()}</p>{/if}
  <div class="scroll-sentinel" bind:this={sentinel} aria-hidden="true">{#if hasMore && loading && items.length}…{/if}</div>
</section>

<style>
  .ngram-heading{display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--line);padding:18px 0}
  .ngram-heading p{display:flex;align-items:baseline;gap:14px;margin:0}
  .ngram-heading b{font-size:40px;font-weight:500;line-height:1.1}
  .ngram-heading b.vertical{writing-mode:vertical-rl;text-orientation:upright}
  .ngram-heading p>span{color:var(--muted);font-size:13px}
  .ngram-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}
  .ngram-occurrence{display:flex;flex-direction:column;background:var(--surface-tile);min-width:0}
  .ngram-page{height:clamp(190px,15vw,270px);padding:12px}
  .ngram-where{margin:0;padding:8px 12px 10px;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ngram-empty{color:var(--muted);padding:30px 0}
  @media(min-width:1700px){.ngram-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
  @media(max-width:1100px){.ngram-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media(max-width:700px){.ngram-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ngram-page{height:200px;padding:10px}}
</style>
