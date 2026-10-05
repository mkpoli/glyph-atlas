<script>
  // Every occurrence of a run of characters: the crops that follow each other on a line, shown as they
  // sit on the page (`RunImage`), with the book and page they come from. The run's own text is written
  // the way most of its occurrences are: down the page or across it. Pages arrive as the reader nears the
  // end, and while more are to come the grid shows whole rows only. A run of many characters gets a
  // taller cell, so its crops stay legible down a column. A card opens the occurrence (`RunOccurrence`):
  // its page, its characters and its source. The run can be narrowed to a group of how its
  // letterforms were made or to a work, and placed by that group or by work, and the sequences near it are one link away.
  import { untrack } from 'svelte'
  import { replaceState } from '$app/navigation'
  import RunImage from './RunImage.svelte'
  import RunOccurrence from './RunOccurrence.svelte'
  import ScriptText from './ScriptText.svelte'
  import SiteLinks from './SiteLinks.svelte'
  import HandFilter from './HandFilter.svelte'
  import WorkFilter from './WorkFilter.svelte'
  import { runAddress, runOccurrences } from '../lib/ngrams.js'
  import { tileDate } from '../lib/dating.js'
  import { collectionAddress } from '../lib/gallery.js'
  import { number } from '../lib/client.js'
  import { sourceTitle } from '../lib/seo.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { text, work: given = '', hand: handed = '', sort: ordered = '', first = null, related = null, inspect } = $props()
  const opened = untrack(() => first)
  let work = $state(untrack(() => given)), hand = $state(untrack(() => handed)), sort = $state(untrack(() => ordered))
  let hands = $state(opened?.hands ?? null), works = $state(opened?.works ?? [])
  let items = $state(opened?.items ?? []), total = $state(opened?.total ?? null), more = $state(opened?.more ?? false)
  let offset = $state(opened?.next_offset ?? 0), vertical = $state(opened?.vertical ?? true), size = $state(opened?.size ?? 2)
  let loading = $state(!opened), error = $state(''), ended = $state(false), requestId = 0
  // The occurrence open in its dialog, and the card it was opened from, which takes the focus back.
  let detail = $state(null), opener = null
  function closeDetail() { detail = null; opener?.focus(); opener = null }

  async function load(append = false) {
    const id = ++requestId
    loading = true; error = ''
    try {
      const page = await runOccurrences(text, { work, hand, sort, offset: append ? offset : 0 })
      if (id !== requestId) return
      items = append ? [...items, ...page.items] : page.items
      // Only the first page carries the count.
      if (!append) { total = page.total; more = page.more; vertical = page.vertical; hands = page.hands; works = page.works; ended = false }
      // A page that reads no further ends the list, so a run whose rows went since its count stops asking.
      if (append && page.next_offset <= offset) ended = true
      offset = page.next_offset; size = page.size
    } catch (e) { if (id === requestId) error = e.message }
    finally { if (id === requestId) loading = false }
  }
  $effect(() => { if (!opened) untrack(() => load()) })
  // A choice reloads the page from its start and is kept in the address, so a copy of it shows the same.
  function choose(change) {
    work = change.work ?? work; hand = change.hand ?? hand; sort = change.sort ?? sort; detail = null
    replaceState(localize(runAddress(text, { work, hand, sort })), {})
    load()
  }
  const near = $derived([
    [() => t('run.near.inside'), related?.inside?.map(run => ({ text: run })) ?? []],
    [() => t('run.near.longer'), related?.longer ?? []],
    [() => t('run.near.siblings', { lead: related?.lead }), related?.siblings ?? []],
  ].filter(([, runs]) => runs.length))

  // An occurrence whose record cannot be read is left off its page, so the pages run on by the rows read.
  const hasMore = $derived(total !== null && offset < total && !ended)
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
  // A corpus glyph's record names its book in `source.title`, a crop's in `source`.
  const where = crop => [sourceTitle(crop), crop.page_number ? t('tile.page', { page: crop.page_number }) : null, tileDate(crop) || null].filter(Boolean).join(' · ')
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
  <div class="run-controls">
    {#if hands}<HandFilter counts={hands} value={hand} onchange={value => choose({ hand: value })} />{/if}
    <div class="run-order">
      <nav class="orders" aria-label={t('run.order.label')}>
        <button class:active={sort !== 'source'} aria-pressed={sort !== 'source'} onclick={() => choose({ sort: '' })}>{t('run.order.hand')}</button>
        <button class:active={sort === 'source'} aria-pressed={sort === 'source'} onclick={() => choose({ sort: 'source' })}>{t('run.order.source')}</button>
      </nav>
      <WorkFilter {works} value={work} onchange={value => choose({ work: value })} />
    </div>
  </div>
  {#if near.length}
    <nav class="run-near" aria-label={t('run.near.label')}>
      {#each near as [label, runs] (runs[0].text)}
        <div class="near-row"><span class="near-label">{label()}</span>
          <ul>{#each runs as run (run.text)}
            <li><a href={localize(runAddress(run.text, { work, hand, sort }))}><ScriptText text={run.text} />{#if run.n}<small>{number(run.n)}</small>{/if}</a></li>
          {/each}</ul>
        </div>
      {/each}
    </nav>
  {/if}
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load(items.length > 0)}>{t('common.tryAgain')}</button></div>{/if}
  <div class="run-grid" bind:this={grid} aria-busy={loading} style:--run-size={size}>
    {#if loading && !items.length}{#each Array(12) as _}<div class="glyph-skeleton"></div>{/each}{/if}
    {#each shown as occurrence (occurrence.crops[0].id)}
      <article class="run-occurrence">
        <button class="run-open" onclick={event => { opener = event.currentTarget; detail = occurrence }} aria-label={[t('run.detail.open', { run: occurrence.crops.map(c => c.label).join('') }), where(occurrence.crops[0])].filter(Boolean).join(' · ')}>
          <span class="run-page"><RunImage crops={occurrence.crops} page={occurrence.page} vertical={occurrence.vertical} /></span>
          <span class="run-where">{where(occurrence.crops[0])}</span>
        </button>
      </article>
    {/each}
  </div>
  {#if !loading && !error && total === 0}<p class="run-empty">{t('run.empty')}</p>{/if}
  <div class="scroll-sentinel" bind:this={sentinel} aria-hidden="true">{#if hasMore && loading && items.length}…{/if}</div>
  {#if detail}<RunOccurrence occurrence={detail} {text} close={closeDetail}
    inspect={(id, origin) => inspect(id, null, crops, null, origin)} />{/if}
</section>

<style>
  .run-heading{display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--line);padding:18px 0}
  .run-heading p{display:flex;align-items:baseline;gap:14px;margin:0}
  .run-heading b{font-size:40px;font-weight:500;line-height:1.1}
  .run-heading b.vertical{writing-mode:vertical-rl;text-orientation:upright}
  .run-heading p>span{color:var(--muted);font-size:13px}
  .run-controls{display:flex;flex-direction:column;gap:2px;padding-bottom:12px}
  .run-order{position:relative;display:flex;align-items:center;gap:16px;padding-top:10px;min-width:0}
  .run-order :global(.work-control){position:static}
  .run-order :global(.work-menu){top:100%;left:0;right:auto;width:min(360px,100%)}
  .orders{display:flex;flex-shrink:0;border:1px solid var(--line);border-radius:8px;padding:2px}
  .orders button{border:0;border-radius:6px;background:transparent;padding:5px 10px;font-size:12px;white-space:nowrap}
  .orders .active{background:var(--surface-selected);color:var(--ink)}
  .run-near{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding:12px 0 14px}
  .near-row{display:flex;align-items:baseline;gap:12px;min-width:0}
  .near-label{flex:0 0 auto;max-width:9em;font-size:12px;color:var(--muted)}
  .near-row ul{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:0;padding:0;min-width:0}
  .near-row a{display:flex;align-items:baseline;gap:6px;border:1px solid var(--line);border-radius:8px;padding:5px 10px;font-size:15px;color:inherit;text-decoration:none}
  .near-row a:hover,.near-row a:focus-visible{background:var(--surface-selected)}
  .near-row small{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .run-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}
  .run-occurrence{display:flex;flex-direction:column;background:var(--surface-tile);min-width:0}
  .run-open{display:flex;flex-direction:column;flex:1;width:100%;border:0;border-radius:0;padding:0;background:transparent;color:inherit;text-align:start;cursor:pointer}
  .run-open:hover,.run-open:focus-visible{background:var(--surface-selected)}
  .run-page{display:block;height:calc(clamp(190px,15vw,270px) + max(0,var(--run-size) - 3) * 36px);padding:12px}
  .run-where{display:block;margin:0;padding:8px 12px 10px;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .run-empty{color:var(--muted);padding:30px 0}
  @media(min-width:1700px){.run-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
  @media(max-width:1100px){.run-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media(max-width:700px){.near-row{flex-direction:column;gap:6px}.near-label{max-width:none}.run-order{flex-wrap:wrap;gap:8px}.run-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.run-page{height:calc(200px + max(0,var(--run-size) - 3) * 30px);padding:10px}}
</style>
