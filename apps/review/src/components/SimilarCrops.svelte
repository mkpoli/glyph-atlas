<script>
  // The crops that look most like this one, and those among them filed under another character,
  // as `atlas similar neighbours` lists them. Loaded once the section comes near the screen.
  import { similar } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { isUnassigned } from '../lib/identity.js'

  // `label` is the crop's current character: a look-alike relabelled to it since the lists were
  // computed is no longer filed differently.
  // `ready` holds the request back until the crop it belongs to can be judged.
  let { id, label = null, ready = true } = $props()
  let open = $state(false), section = $state(null)
  let result = $state(null)
  let failed = $state(false)
  let tab = $state('similar')
  let loaded = null

  async function load() {
    if (!open || !ready || loaded === id) return
    const wanted = id
    loaded = wanted
    result = null
    failed = false
    try {
      const value = await similar(wanted)
      if (loaded === wanted) result = value
    } catch {
      // A failed load is asked again when the dialog moves to this crop again.
      if (loaded === wanted) { failed = true; loaded = null }
    }
  }
  $effect(() => { id; open; ready; load() })
  $effect(() => {
    if (!section) return
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) { open = true; observer.disconnect() } },
      // The dialog is what scrolls, so the margin is measured against it.
      { root: section.closest('dialog'), rootMargin: '0px 0px 400px 0px' })
    observer.observe(section)
    return () => observer.disconnect()
  })
  const list = $derived(!result ? [] : tab === 'similar' ? result.similar ?? []
    : (result.filed_differently ?? []).filter(item => !label || item.label !== label))
  // One row a page, turned with ‹ ›: the section keeps one height and never scrolls.
  let width = $state(0), pageAt = $state(0)
  const per = $derived(Math.max(1, Math.floor((width + 6) / 78)))
  const pages = $derived(Math.max(1, Math.ceil(list.length / per)))
  $effect(() => { id; tab; pageAt = 0 })
  const at = $derived(Math.min(pageAt, pages - 1))
  const visible = $derived(list.slice(at * per, at * per + per))
  const shown = item => isUnassigned(item) ? t('corpus.unassigned') : item.label ?? ''
  const href = item => localize((item.origin === 'corpus' ? '/corpus/' : '/crop/') + encodeURIComponent(item.id))
  const empty = $derived(result?.revision === null ? t('similar.notPublished')
    : tab === 'similar' ? t('similar.none') : t('similar.noneFiledDifferently'))
</script>

<!-- A list that could not be read is left out; the next crop asks again. -->
{#if !failed}<section class="similar-crops" bind:this={section} aria-label={t('similar.title')}>
  <h3>{t('similar.title')}</h3>
  {#if !result}<div class="similar-skeleton"></div>
  {:else}
    <div class="similar-tabs">
      <button aria-pressed={tab === 'similar'} onclick={() => tab = 'similar'}>{t('similar.tab.similar')}</button>
      <button aria-pressed={tab === 'filed_differently'} onclick={() => tab = 'filed_differently'}>{t('similar.tab.filedDifferently')}</button>
      {#if pages > 1}<span class="similar-pages"><button aria-label={t('similar.previous')} disabled={at === 0} onclick={() => pageAt = at - 1}>‹</button><button aria-label={t('similar.next')} disabled={at >= pages - 1} onclick={() => pageAt = at + 1}>›</button></span>{/if}
    </div>
    {#if list.length}
      <ul class="similar-grid" bind:clientWidth={width}>
        {#each visible as item (item.id)}
          <li><a href={href(item)} target="_blank" rel="noreferrer" aria-label={t('similar.open', { label: shown(item) })}>
            {#if item.image && (item.origin !== 'corpus' || item.proxyable)}<img src={item.image} alt="" loading="lazy" decoding="async" />{:else}<span class="similar-missing" lang="ja">{shown(item)}</span>{/if}
            <span class="similar-label" lang={isUnassigned(item) ? undefined : 'ja'}>{shown(item)}</span><small>{item.score.toFixed(2)}</small>
          </a></li>
        {/each}
      </ul>
    {:else}<p class="similar-note">{empty}</p>{/if}
  {/if}
</section>{/if}

<style>
  .similar-crops{margin:24px 0 0}.similar-crops h3{margin:0;font-size:12px;font-weight:500;color:var(--muted)}
  .similar-note{color:var(--muted);font-size:13px;margin-top:8px;min-height:118px}
  .similar-skeleton{height:158px;border-radius:8px;background:var(--surface-disabled);margin-top:8px}
  .similar-tabs{display:flex;gap:6px;margin:8px 0}
  .similar-tabs button{font-size:11px;padding:7px 10px;border-radius:5px;background:var(--surface-disabled);border-color:transparent}
  .similar-tabs button[aria-pressed="true"]{color:var(--accent);background:var(--accent-light)}
  .similar-pages{display:flex;gap:4px;margin-left:auto}
  .similar-pages button{font-size:14px;padding:3px 10px;border-radius:5px;background:transparent;border-color:var(--line)}
  .similar-grid{list-style:none;padding:3px;margin:0;display:flex;gap:6px;height:118px;overflow:hidden}
  .similar-grid li{flex:0 0 72px}
  .similar-grid a:focus-visible{outline-offset:-3px}
  .similar-grid a{display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px;border:1px solid var(--line);border-radius:6px;color:inherit;text-decoration:none}
  .similar-grid img,.similar-missing{width:60px;height:60px;object-fit:contain;display:grid;place-items:center;font-size:28px}
  .similar-grid img{background:#fff}
  .similar-label{font-size:14px}.similar-grid small{color:var(--muted);font-size:11px}
</style>
