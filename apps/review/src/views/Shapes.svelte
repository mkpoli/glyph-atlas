<script>
  // The crops given a 構形 and no form, by 構形: their shapes wait to be named. Opening one steps through
  // the page, and one whose form is named, or whose 構形 is taken back, leaves it.
  import Glyph from '../components/Glyph.svelte'
  import IdsLabel from '../components/IdsLabel.svelte'
  import ScriptText from '../components/ScriptText.svelte'
  import { page } from '$app/state'
  import { request } from '../lib/client.js'
  import { builtAs } from '../lib/cropForms.js'
  import { isUnassigned } from '../lib/identity.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { useInspector } from '../lib/inspector.svelte.js'
  const inspector = useInspector()
  const wanted = $derived(page.url.searchParams.get('structure') ?? '')
  let items = $state([]), next = $state(null), narrowed = $state(null), loading = $state(true), error = $state(''), asked = 0
  const label = item => isUnassigned(item) ? t('corpus.unassigned') : item.label
  const groups = $derived(items.reduce((list, item) => {
    if (list.at(-1)?.id === item.structure.id) list.at(-1).items.push(item)
    else list.push({ ...item.structure, items: [item] })
    return list
  }, []))

  // A page can hold only crops whose forms are named by now: the next ones are read until a crop shows, at
  // most EMPTY in a row.
  const EMPTY = 5
  async function load(after = null, empty = 0) {
    const turn = ++asked
    loading = true; error = ''
    if (!after) { items = []; next = null }
    try {
      const found = await request('/atlas/shapes?' + new URLSearchParams({ ...(wanted ? { structure: wanted } : {}), ...(after ? { after } : {}) }))
      if (turn !== asked) return
      items = after ? [...items, ...found.items] : found.items
      next = found.next; narrowed = found.structure
      if (!found.items.length && next && empty < EMPTY) await load(next, empty + 1)
    } catch (failure) { if (turn === asked) error = failure.message } finally { if (turn === asked) loading = false }
  }
  $effect(() => { void wanted; load() })
  // A save that names the form, or gives another 構形 or none, takes the crop off its place here.
  // A review's answer says nothing of forms, and a redrawn box is another cut, so the crop is read again.
  async function update(id, record, kind) {
    const item = items.find(each => each.id === id || each.id === page.state.inspect?.was)
    if (!item || !record || kind === 'skipped') return
    try { if (!('structure' in record)) record = await request('/atlas/characters/' + encodeURIComponent(id)) } catch (failure) { if (failure.status !== 404) return; record = {} }
    const held = builtAs(record)
    if (id !== item.id || held.length !== 1 || held[0].structure !== item.structure.id) items = items.filter(each => each !== item)
  }
  const open = item => inspector.inspect(item.id, items, update)
</script>

<section class="explore shapes">
  <header class="shapes-head"><h1>{t('shapes.title')}</h1>
    {#if wanted}<span class="shapes-of">{#if narrowed}<IdsLabel text={narrowed.label} />{/if}<a class="quiet-link" href={localize('/shapes')}>{t('shapes.all')}</a></span>{/if}
  </header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if loading && !items.length}<div class="glyph-grid" aria-busy="true">{#each Array(8) as _}<div class="glyph-skeleton"></div>{/each}</div>
  {:else if !items.length && !next && !error}<div class="empty"><span class="empty-mark">？</span><h2>{t('shapes.empty')}</h2></div>
  {:else}
    <div class="shapes-groups">
    {#each groups as group (group.id)}
      <section class="shape" aria-label={group.label}>
        <h2><IdsLabel text={group.label} /></h2>
        <div class="glyph-grid">
          {#each group.items as item, i (item.id)}
            <button class="glyph-tile" class:corpus={item.origin === 'corpus'} onclick={() => open(item)} aria-label={t('explore.tile.inspect', { label: label(item) })}>
              <span class="tile-label"><span class="tile-glyph" class:unassigned={isUnassigned(item)}>{#if isUnassigned(item)}{label(item)}{:else}<ScriptText text={item.label} />{/if}</span></span>
              {#if item.image && (item.origin !== 'corpus' || item.proxyable)}<Glyph {item} eager={i < 24} />{:else}<span class="corpus-open"><b>{label(item)}</b><small>{t('character.image.unavailable')}</small></span>{/if}
              <span class="tile-footer"><span class="tile-arrow">↗</span></span>
            </button>
          {/each}
        </div>
      </section>
    {/each}
    </div>
    {#if next}<div class="load-more"><button disabled={loading} onclick={() => load(next)}>{t('favourites.more')}</button></div>{/if}
  {/if}
</section>

<style>
  .shapes{padding-bottom:48px}
  /* A short last row ends at its last tile. */
  .shapes :global(.glyph-grid){background:none;border:0}
  .shapes :global(.glyph-tile){outline:1px solid var(--line)}
  .shapes-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:16px}
  .shapes-head h1{font-size:28px;font-weight:500;letter-spacing:-.6px}
  .shapes-of{display:inline-flex;align-items:baseline;gap:12px;font-family:var(--char-font);font-size:18px}
  .shapes-of a{font-family:var(--font,inherit);font-size:12px}
  /* A 構形 takes the width of its crops, so those with few stand side by side. */
  .shapes-groups{display:flex;flex-wrap:wrap;gap:28px 20px}
  .shape{min-width:0}
  .shape .glyph-grid{display:flex;flex-wrap:wrap;gap:10px}
  .shape .glyph-tile{width:min(168px,calc(50vw - 30px))}
  .shape h2{font-family:var(--char-font);font-size:18px;font-weight:400;margin-bottom:10px}
</style>
