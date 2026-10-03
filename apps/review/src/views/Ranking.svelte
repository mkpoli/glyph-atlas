<script>
  import { onMount } from 'svelte'
  import { request, number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { useSession } from '../lib/session.svelte.js'
  const session = useSession()
  let items = $state([]), loading = $state(true), error = $state('')
  const initial = name => [...(name || '?')][0].toUpperCase()
  async function load() {
    loading = true; error = ''
    try { items = (await request('/api/ranking')).items } catch (failure) { error = failure.message } finally { loading = false }
  }
  onMount(load)
</script>

<section class="explore ranking">
  <header class="ranking-head"><p class="overline">{t('ranking.overline')}</p><h1>{t('ranking.title')}</h1><p class="ranking-hint">{t('ranking.hint')}</p></header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={load}>{t('common.retry')}</button></div>{/if}
  {#if loading && !items.length}<p class="find-count" role="status">{t('history.loading')}</p>
  {:else if !items.length}<div class="empty"><span class="empty-mark">∅</span><h2>{t('ranking.empty')}</h2></div>
  {:else}
    <ol class="ranking-list">
      {#each items as row (row.user ?? row.name)}
        <li class:mine={row.user && row.user === session.state.user?.id} class:podium={row.place <= 3}>
          <span class="ranking-place">{number(row.place)}</span>
          <span class="avatar ranking-avatar" class:unnamed={row.anonymous} aria-hidden="true">{#if row.image}<img src={row.image} alt="" loading="lazy" referrerpolicy="no-referrer" />{:else}{row.anonymous ? '?' : initial(row.name)}{/if}</span>
          <span class="ranking-who">
            {#if row.anonymous}<span>{t('ranking.anonymous')}</span><small>{row.name}</small>{:else}<span>{row.name}</span>{/if}
          </span>
          {#if row.user}<a class="quiet-link ranking-history" href={localize('/history') + '?user=' + encodeURIComponent(row.user)}>{t('nav.history')} ↗</a>{/if}
          <span class="ranking-total">{t('ranking.total', { count: row.total })}</span>
        </li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  .ranking { max-width: 820px; padding-bottom: 48px; }
  .ranking-head h1 { font-size: 28px; font-weight: 500; letter-spacing: -.6px; margin: 6px 0 6px; }
  .ranking-hint { font-size: 12px; line-height: 1.5; color: var(--muted); margin-bottom: 18px; }
  .ranking-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
  .ranking-list li { display: flex; align-items: center; gap: 14px; padding: 12px 8px; border-bottom: 1px solid var(--line); font-size: 13px; }
  .ranking-list li.mine { background: var(--accent-hover); }
  .ranking-place { flex: 0 0 34px; text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; }
  .podium .ranking-place { color: var(--ink); font-weight: 600; font-size: 15px; }
  .ranking-avatar.unnamed { background: var(--surface-badge); color: var(--muted); }
  .podium .ranking-avatar { box-shadow: 0 0 0 2px var(--accent-light); }
  .ranking-who { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .ranking-who > * { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ranking-who small { font-size: 10px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  .ranking-history { font-size: 11px; white-space: nowrap; }
  .ranking-total { flex: 0 0 auto; font-variant-numeric: tabular-nums; font-weight: 500; }
  @media (max-width: 700px) { .ranking-history { display: none; } .ranking-list li { gap: 10px; } }
</style>
