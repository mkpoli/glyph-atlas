<script>
  // The crops that look most like this one, and those among them filed under another character,
  // as `atlas similar neighbours` lists them. Loaded when the section is first opened.
  import { similar } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'

  let { id } = $props()
  let open = $state(false)
  let result = $state(null)
  let failed = $state(false)
  let tab = $state('similar')
  let loaded = null

  async function load() {
    if (!open || loaded === id) return
    const wanted = id
    loaded = wanted
    result = null
    failed = false
    try {
      const value = await similar(wanted)
      if (loaded === wanted) result = value
    } catch {
      if (loaded === wanted) failed = true
    }
  }
  $effect(() => { id; open; load() })
  const list = $derived(result ? result[tab] ?? [] : [])
  const href = item => localize((item.origin === 'corpus' ? '/corpus/' : '/crop/') + encodeURIComponent(item.id))
</script>

<details class="similar-crops" bind:open>
  <summary>{t('similar.title')}</summary>
  {#if failed}<p class="similar-note">{t('similar.unavailable')}</p>
  {:else if !result}<div class="similar-skeleton" aria-busy="true"></div>
  {:else}
    <div class="similar-tabs" role="tablist">
      <button role="tab" aria-selected={tab === 'similar'} class:active={tab === 'similar'} onclick={() => tab = 'similar'}>{t('similar.tab.similar')}</button>
      <button role="tab" aria-selected={tab === 'filed_differently'} class:active={tab === 'filed_differently'} onclick={() => tab = 'filed_differently'}>{t('similar.tab.filedDifferently')}</button>
    </div>
    {#if list.length}
      <ul class="similar-grid">
        {#each list as item (item.id)}
          <li><a href={href(item)} target="_blank" rel="noreferrer" aria-label={t('similar.open', { label: item.label })}>
            {#if item.image && (item.origin !== 'corpus' || item.proxyable)}<img src={item.image} alt="" loading="lazy" decoding="async" />{:else}<span class="similar-missing" lang="ja">{item.label}</span>{/if}
            <span class="similar-label" lang="ja">{item.label}</span><small>{item.score.toFixed(2)}</small>
          </a></li>
        {/each}
      </ul>
    {:else}<p class="similar-note">{t('similar.none')}</p>{/if}
  {/if}
</details>

<style>
  .similar-crops{margin:12px 0}.similar-crops summary{cursor:pointer;font-weight:600}
  .similar-note{color:var(--muted);font-size:13px}
  .similar-skeleton{height:96px;border-radius:8px;background:var(--line);opacity:.4;margin-top:8px}
  .similar-tabs{display:flex;gap:6px;margin:8px 0}
  .similar-tabs button{font-size:11px;padding:7px 10px;border-radius:5px;background:var(--surface-disabled);border-color:transparent}
  .similar-tabs button.active{color:var(--accent);background:var(--accent-light)}
  .similar-grid{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(72px,1fr));gap:6px}
  .similar-grid a{display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px;border:1px solid var(--line);border-radius:6px;color:inherit;text-decoration:none}
  .similar-grid img,.similar-missing{width:60px;height:60px;object-fit:contain;display:grid;place-items:center;font-size:28px}
  .similar-grid img{background:#fff}
  .similar-label{font-size:14px}.similar-grid small{color:var(--muted);font-size:11px}
</style>
