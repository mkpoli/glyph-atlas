<script>
  // The crops the signed-in user has starred, newest first. Opening one steps through the page.
  import Glyph from '../components/Glyph.svelte'
  import ScriptText from '../components/ScriptText.svelte'
  import { request } from '../lib/client.js'
  import { isFavourite } from '../lib/favourites.svelte.js'
  import { isUnassigned } from '../lib/identity.js'
  import { t } from '../lib/i18n.svelte.js'
  import { useInspector } from '../lib/inspector.svelte.js'
  import { sessionStarted, useSession } from '../lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
  let items = $state([]), total = $state(0), next = $state(0), loading = $state(true), error = $state('')
  // A crop unstarred in the inspector leaves the page; it stays in the inspector's queue.
  const shown = $derived(items.filter(item => isFavourite(item.id)))
  const label = item => isUnassigned(item) ? t('corpus.unassigned') : item.label

  async function load(offset = 0) {
    loading = true; error = ''
    try {
      await sessionStarted()
      const page = await request('/api/favourites/crops?' + new URLSearchParams({ offset: String(offset), limit: '60' }))
      items = offset ? [...items, ...page.items] : page.items
      total = page.total; next = page.next
    } catch (failure) { error = failure.message } finally { loading = false }
  }
  // Read again when another user signs in.
  $effect(() => { session.state.user?.id; load() })
  const open = item => inspector.inspect(item.id, null, shown, null, item.origin === 'corpus' ? 'corpus' : 'collection')
</script>

<section class="explore favourites">
  <header class="favourites-head"><h1>{t('favourites.title')}</h1>{#if total}<p class="collection-meta">{t('favourites.count', { count: shown.length + Math.max(0, total - items.length) })}</p>{/if}</header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if loading && !items.length}<div class="glyph-grid" aria-busy="true">{#each Array(8) as _}<div class="glyph-skeleton"></div>{/each}</div>
  {:else if !shown.length}<div class="empty"><span class="empty-mark">☆</span><h2>{t('favourites.empty')}</h2><p class="favourites-hint">{t('favourites.hint')}</p></div>
  {:else}
    <div class="glyph-grid" aria-label={t('favourites.title')}>
      {#each shown as item, i (item.id)}
        <button class="glyph-tile" class:corpus={item.origin === 'corpus'} onclick={() => open(item)} aria-label={t('explore.tile.inspect', { label: label(item) })}>
          <span class="tile-label"><span class="tile-glyph" class:unassigned={isUnassigned(item)}>{#if isUnassigned(item)}{label(item)}{:else}<ScriptText text={item.label} />{/if}</span></span>
          {#if item.image && (item.origin !== 'corpus' || item.proxyable)}<Glyph {item} eager={i < 24} />{:else}<span class="corpus-open"><b>{label(item)}</b><small>{t('character.image.unavailable')}</small></span>{/if}
          <span class="tile-footer"><span class="tile-arrow">↗</span></span>
        </button>
      {/each}
    </div>
    {#if next !== null}<div class="load-more"><button disabled={loading} onclick={() => load(next)}>{t('favourites.more')}</button></div>{/if}
  {/if}
</section>

<style>
  .favourites{padding-bottom:48px}
  /* A short last row ends at its last tile. */
  .favourites :global(.glyph-grid){background:none;border:0}
  .favourites :global(.glyph-tile){outline:1px solid var(--line)}
  .favourites-head{display:flex;align-items:baseline;gap:16px;margin-bottom:16px}
  .favourites-head h1{font-size:28px;font-weight:500;letter-spacing:-.6px}
  .favourites-hint{font-size:13px;color:var(--muted)}
</style>
