<script>
  import { onMount } from 'svelte'
  import Glyph from '../components/Glyph.svelte'
  import { catalogue, character, randomSeed, number } from '../lib/client.js'
  let { flagged = false, inspect } = $props()
  let data = $state(null), items = $state([]), error = $state(''), loading = $state(true)
  let reading = $state(''), search = $state(''), offset = $state(0), seed = $state(randomSeed())
  let categoryOpen = $state(false), filter = $state('all'), requestId = 0, closed = false
  const categories = $derived((data?.categories ?? []).filter(c => c.label.includes(search) && (!flagged || c.flagged)))
  const display = $derived(items)
  async function load(append = false) {
    const id = ++requestId; loading = true; error = ''
    try {
      const result = await catalogue({ reading, group: filter, state: flagged ? 'flagged' : 'all', seed, offset, limit: 60 })
      if (closed || id !== requestId) return
      data = result; items = append ? [...items, ...result.items] : result.items
    } catch (e) { if (!closed) error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  async function updateItem(id) {
    try {
      const updated = await character(id)
      items = items.flatMap(item => item.id !== id ? [item] : flagged && updated.state !== 'flagged' ? [] : [updated])
      const summary = await catalogue({ reading, group: filter, state: flagged ? 'flagged' : 'all', limit: 1 })
      if (!closed) data = { ...data, counts: summary.counts, categories: summary.categories, total: summary.total, available: summary.available }
    } catch (e) { if (!closed) error = e.message }
  }
  function select(value) { reading = value; offset = 0; categoryOpen = false; load() }
  function shuffle() { seed = randomSeed(); offset = 0; load() }
  onMount(() => { load(); return () => { closed = true } })
</script>

<section class="explore">
  <div class="explore-heading">
    <div><p class="overline">{flagged ? 'YOUR REVIEW QUEUE' : 'THE COLLECTION'}</p><h1>{flagged ? 'A closer look.' : 'Character atlas.'}</h1></div>
    <div class="collection-meta"><span class="live-dot"></span><span>{number(flagged ? data?.total : data?.available)} glyphs</span><span class="meta-divider">/</span><span>{number(data?.categories.length)} readings</span></div>
  </div>
  <div class="collection-toolbar">
    <div class="filter-tabs" aria-label="Character type">{#each [['all', 'All'], ['kana', 'Kana'], ['kanji', 'Kanji']] as [value, text]}<button class:active={filter === value} onclick={() => { filter = value; offset = 0; load() }}>{text}</button>{/each}</div>
    <div class="category-control"><button class="category-toggle" aria-expanded={categoryOpen} onclick={() => categoryOpen = !categoryOpen}>{reading || 'Any reading'} <span>⌄</span></button>
      {#if categoryOpen}<div class="category-menu"><input aria-label="Find a reading" bind:value={search} placeholder="Find a reading…" /><button class="all-readings" onclick={() => select('')}>All readings</button><div class="category-options">{#each categories as c}<button class:chosen={reading === c.label} onclick={() => select(c.label)}><span>{c.label}</span><small>{number(flagged ? c.flagged : c.total)}</small></button>{/each}</div></div>{/if}
    </div>
    <span class="toolbar-space"></span>
    {#if reading && !flagged}<a class="quiet-link" href={`#/review?reading=${encodeURIComponent(reading)}`}>Review {reading} ↗</a>{/if}
    <button class="shuffle" onclick={shuffle} disabled={loading} aria-label="Shuffle characters"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 6h3c4 0 8 12 12 12h3M17 14l4 4-4 4M3 18h3c1.7 0 3.5-2.3 5-5M14 8c1.5-1.4 2.6-2 4-2h3M17 2l4 4-4 4"/></svg>Shuffle</button>
  </div>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>Retry</button></div>{/if}
  <div class="glyph-grid" aria-label={flagged ? 'Flagged characters' : 'Character collection'} aria-busy={loading}>
    {#if loading && !items.length}{#each Array(32) as _}<div class="glyph-skeleton"></div>{/each}
    {:else}{#each display as item, i (item.id)}<button class="glyph-tile" onclick={() => inspect(item.id, null, items, updateItem)} aria-label={`Inspect ${item.label}`}><span class="tile-reading">{item.label}</span><Glyph {item} eager={i < 24} /><span class="tile-footer"><span class="status-dot" class:checked={item.state === 'checked'} class:flagged={item.state === 'flagged'}></span><span>{String(i + 1).padStart(2, '0')}</span><span class="tile-arrow">↗</span></span></button>{/each}{/if}
  </div>
  {#if !loading && !display.length}<div class="empty"><span class="empty-mark">{flagged ? '✓' : '∅'}</span><h2>{flagged ? 'Nothing flagged.' : 'No characters here.'}</h2><a href="#/review" class="primary">Start a round →</a></div>{/if}
  {#if data && items.length < data.total && !loading}<div class="load-more"><button onclick={() => { offset = items.length; load(true) }}>More characters ↓</button></div>{/if}
  <div class="collection-bottom"><span>GLYPH ATLAS</span><span>{number(data?.counts.checked)} checked <span class="separator">·</span> {number(data?.counts.flagged)} flagged</span></div>
</section>
