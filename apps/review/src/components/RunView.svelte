<script>
  // Every occurrence of a run of characters: the crops that follow each other on a line, shown as they
  // sit on the page (`RunImage`), with the book and page they come from. The run's own text is written
  // the way most of its occurrences are: down the page or across it. Pages arrive as the reader nears the
  // end, and while more are to come the grid shows whole rows only. A run of many characters gets a
  // taller cell, so its crops stay legible down a column.
  import { untrack } from 'svelte'
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import SiteLinks from './SiteLinks.svelte'
  import { runOccurrences } from '../lib/ngrams.js'
  import { tileDate } from '../lib/dating.js'
  import { collectionAddress } from '../lib/gallery.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { text, work = '', first = null, inspect } = $props()
  const opened = untrack(() => first)
  let items = $state(opened?.items ?? []), total = $state(opened?.total ?? null), more = $state(opened?.more ?? false)
  let offset = $state(opened?.next_offset ?? 0), vertical = $state(opened?.vertical ?? true), size = $state(opened?.size ?? 2)
  let loading = $state(!opened), error = $state(''), requestId = 0

  async function load(append = false) {
    const id = ++requestId
    loading = true; error = ''
    try {
      const page = await runOccurrences(text, { work, offset: append ? offset : 0 })
      if (id !== requestId) return
      items = append ? [...items, ...page.items] : page.items
      // Only the first page carries the count.
      if (!append) { total = page.total; more = page.more; vertical = page.vertical }
      offset = page.next_offset; size = page.size
    } catch (e) { if (id === requestId) error = e.message }
    finally { if (id === requestId) loading = false }
  }
  $effect(() => { if (!opened) untrack(() => load()) })

  const hasMore = $derived(total !== null && items.length < total)
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

<section class="explore run-view">
  <div class="page-status">
    <h1 class="visually-hidden">{text}</h1>
    <SiteLinks />
  </div>
  <header class="run-heading">
    <a class="quiet-link" href={localize(collectionAddress({ work }))}>← {t('nav.explore')}</a>
    <p><b class:vertical><ScriptText {text} /></b>{#if total !== null}<span>{more ? t('run.occurrences.more', { count: total }) : t('run.occurrences', { count: total })}</span>{/if}</p>
  </header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load(items.length > 0)}>{t('common.tryAgain')}</button></div>{/if}
  <div class="run-grid" bind:this={grid} aria-busy={loading} style:--run-size={size}>
    {#if loading && !items.length}{#each Array(12) as _}<div class="glyph-skeleton"></div>{/each}{/if}
    {#each shown as occurrence (occurrence.crops[0].id)}
      <article class="run-occurrence">
        <div class="run-page"><RunImage crops={occurrence.crops} page={occurrence.page} vertical={occurrence.vertical} oninspect={id => inspect(id, null, crops)} /></div>
        <p class="run-where">{where(occurrence.crops[0])}</p>
      </article>
    {/each}
  </div>
  {#if !loading && !error && total === 0}<p class="run-empty">{t('run.empty')}</p>{/if}
  <div class="scroll-sentinel" bind:this={sentinel} aria-hidden="true">{#if hasMore && loading && items.length}…{/if}</div>
</section>

<style>
  .run-heading{display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--line);padding:18px 0}
  .run-heading p{display:flex;align-items:baseline;gap:14px;margin:0}
  .run-heading b{font-size:40px;font-weight:500;line-height:1.1}
  .run-heading b.vertical{writing-mode:vertical-rl;text-orientation:upright}
  .run-heading p>span{color:var(--muted);font-size:13px}
  .run-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}
  .run-occurrence{display:flex;flex-direction:column;background:var(--surface-tile);min-width:0}
  .run-page{height:calc(clamp(190px,15vw,270px) + max(0,var(--run-size) - 3) * 36px);padding:12px}
  .run-where{margin:0;padding:8px 12px 10px;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .run-empty{color:var(--muted);padding:30px 0}
  @media(min-width:1700px){.run-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
  @media(max-width:1100px){.run-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media(max-width:700px){.run-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.run-page{height:calc(200px + max(0,var(--run-size) - 3) * 30px);padding:10px}}
</style>
