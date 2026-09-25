<script>
  import { onMount } from 'svelte'
  import { history as fetchHistory, stored, remember } from '../lib/client.js'
  import { issues } from '../lib/issues.js'
  import { t, locale } from '../lib/i18n.svelte.js'
  let { clientId, inspect } = $props()
  let items = $state([]), loading = $state(true), loadingMore = $state(false), error = $state('')
  let cursor = $state(null), hasMore = $state(true)
  let onlyMine = $state(stored('atlas.history.onlyMine', false))
  let characterFilter = $state('')
  let requestId = 0, closed = false, filterTimer
  // True while the load-more row is on screen or close to it: scrolling down loads the next page.
  let nearEnd = $state(false)
  function watchEnd(node) {
    const observer = new IntersectionObserver(entries => { nearEnd = entries.some(entry => entry.isIntersecting) },
      { rootMargin: '0px 0px 600px 0px' })
    observer.observe(node)
    return { destroy() { observer.disconnect(); nearEnd = false } }
  }
  async function load(append = false) {
    const id = ++requestId
    if (append) loadingMore = true
    else { loading = true; items = []; cursor = null; hasMore = true }
    error = ''
    try {
      const result = await fetchHistory({
        limit: 40, before: append ? cursor : undefined,
        actor: onlyMine ? clientId : undefined, label: characterFilter.trim() || undefined,
      })
      if (closed || id !== requestId) return
      items = append ? [...items, ...result.items] : result.items
      cursor = result.next; hasMore = Boolean(result.next)
    } catch (e) { if (!closed && id === requestId) error = e.message }
    finally { if (!closed && id === requestId) { loading = false; loadingMore = false } }
  }
  function toggleMine() { onlyMine = !onlyMine; remember('atlas.history.onlyMine', onlyMine); load() }
  function seekCharacter(value) {
    characterFilter = value
    clearTimeout(filterTimer)
    filterTimer = setTimeout(() => load(), 250)
  }
  const hasIssueTitle = id => issues.some(issue => issue.id === id)
  /** What a row says was decided: a written-character correction and a reading correction each name
   * what they changed to; otherwise the issue that was reported, or the plain verdict. */
  function decisionText(item) {
    if (item.kind === 'undo') return t('history.decision.undo')
    if (item.character) return t('history.decision.wrongCharacter', { character: item.character })
    if (item.issue === 'reading' && item.reading) return t('history.decision.wrongReading', { reading: item.reading })
    if (item.issue && hasIssueTitle(item.issue)) return t(`issue.${item.issue}.title`)
    if (item.verdict === 'match') return t('history.decision.match')
    if (item.verdict === 'unsure') return t('history.decision.unsure')
    return t('history.decision.reported')
  }
  const formatter = $derived(new Intl.DateTimeFormat(locale(), { dateStyle: 'medium', timeStyle: 'short' }))
  function when(at) { try { return formatter.format(new Date(at)) } catch { return at } }
  $effect(() => { if (nearEnd && hasMore && !error && !loading && !loadingMore) load(true) })
  onMount(() => { load(); return () => { closed = true; clearTimeout(filterTimer) } })
</script>

<section class="explore history">
  <h1 class="visually-hidden">{t('history.heading')}</h1>
  <div class="collection-toolbar">
    <div class="filter-tabs" aria-label={t('history.onlyMine.aria')}>
      <button class:active={onlyMine} aria-pressed={onlyMine} onclick={toggleMine}>{t('history.onlyMine')}</button>
    </div>
    <input class="history-character-filter" type="text" lang="ja" value={characterFilter}
           oninput={e => seekCharacter(e.currentTarget.value)}
           placeholder={t('history.characterFilter.placeholder')} aria-label={t('history.characterFilter.aria')} />
  </div>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if loading && !items.length}
    <p class="find-count" role="status">{t('history.loading')}</p>
  {:else if !items.length}
    <div class="empty"><span class="empty-mark">∅</span><h2>{t('history.empty')}</h2></div>
  {:else}
    <ul class="history-list">
      {#each items as item (item.id)}
        <li>
          <button class="history-row" class:undo={item.kind === 'undo'} onclick={() => inspect(item.target)}
                  aria-label={t('history.row.inspect', { label: item.label ?? item.target })}>
            <span class="history-time">{when(item.at)}</span>
            <span class="history-actor">{item.actor}</span>
            <span class="history-label" lang="ja">{item.label ?? t('history.noLabel')}</span>
            <span class="history-decision">{decisionText(item)}</span>
          </button>
        </li>
      {/each}
    </ul>
    <div class="load-more-row" use:watchEnd>
      {#if loadingMore}<p class="find-count" role="status">{t('history.loading')}</p>
      {:else if !hasMore}<p class="find-count" role="status">{t('history.noMore')}</p>{/if}
    </div>
  {/if}
</section>

<style>
  .history { padding-bottom: 40px; }
  .history-character-filter { max-width: 220px; font-size: 13px; }
  .history-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
  .history-list li { border-bottom: 1px solid var(--line); }
  .history-row {
    display: flex; align-items: center; gap: 20px; width: 100%; border: 0; border-radius: 0;
    background: transparent; padding: 14px 6px; font-size: 13px; text-align: left;
  }
  .history-row:hover { background: #f4f4f5; }
  .history-row.undo .history-decision { color: var(--wrong); }
  .history-time { flex: 0 0 150px; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  .history-actor { flex: 0 0 130px; color: var(--muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .history-label { flex: 0 0 40px; font-size: 20px; text-align: center; }
  .history-decision { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .load-more-row { display: flex; justify-content: center; padding: 22px 0; }
  @media (max-width: 700px) {
    .history-time { flex-basis: 90px; }
    .history-actor { display: none; }
  }
</style>
