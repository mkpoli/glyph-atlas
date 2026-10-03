<script>
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { page } from '$app/state'
  import { history as fetchHistory, request, stored, remember } from '../lib/client.js'
  import { issues } from '../lib/issues.js'
  import ScriptText from '../components/ScriptText.svelte'
  import ScriptLine from '../components/ScriptLine.svelte'
  import { t, withText, formatDateTime, localize } from '../lib/i18n.svelte.js'
  import { groupRuns } from '../lib/historyGroups.js'
  let { inspect } = $props()
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
    shownFor = userFilter
    const id = ++requestId
    if (append) loadingMore = true
    else { loading = true; items = []; cursor = null; hasMore = true }
    error = ''
    try {
      const result = await fetchHistory({
        limit: 40, before: append ? cursor : undefined,
        mine: (onlyMine && !userFilter) || undefined, user: userFilter || undefined, label: characterFilter.trim() || undefined,
      })
      if (closed || id !== requestId) return
      items = append ? [...items, ...result.items] : result.items
      cursor = result.next; hasMore = Boolean(result.next)
    } catch (e) { if (!closed && id === requestId) error = e.message }
    finally { if (!closed && id === requestId) { loading = false; loadingMore = false } }
  }
  // `?user=` narrows the history to one reviewer, as the admin page links to it; it takes the place of
  // "Only mine" while it is set.
  const userFilter = $derived(page.url.searchParams.get('user') ?? '')
  let shownFor = null
  $effect(() => { const wanted = userFilter; if (shownFor !== null && wanted !== shownFor) load() })
  const clearUser = () => goto(localize('/history'), { replaceState: true, noScroll: true, keepFocus: true })
  const initial = name => [...(name || '?')][0].toUpperCase()
  function toggleMine() { onlyMine = !onlyMine; remember('atlas.history.onlyMine', onlyMine); load() }
  function seekCharacter(value) {
    characterFilter = value
    clearTimeout(filterTimer)
    filterTimer = setTimeout(() => load(), 250)
  }
  const hasIssueTitle = id => issues.some(issue => issue.id === id)
  /** What a row says was decided: a written-character correction names what it changed to, for
   * `ScriptLine` to colour; otherwise the issue that was reported, or the plain verdict. */
  function decisionText(item) {
    if (item.kind === 'undo') return t('history.decision.undo')
    if (item.kind === 'passed') return t('history.decision.passed', { count: item.passed })
    if (item.character) return withText('history.decision.wrongCharacter', 'character', { character: item.character })
    // A wrong-character review saved before characters were named on their own kept the typed text.
    if (item.issue === 'character' && item.text) return withText('history.decision.wrongCharacter', 'character', { character: item.text })
    if (item.issue && hasIssueTitle(item.issue)) return t(`issue.${item.issue}.title`)
    if (item.verdict === 'match') return t('history.decision.match')
    if (item.verdict === 'unsure') return t('history.decision.unsure')
    return t('history.decision.reported')
  }
  // A batch correction's edits arrive together; they read as one row, undone together.
  const rows = $derived(items.reduce((list, item) => {
    const last = list.at(-1)
    if (item.batch && last?.batch === item.batch) last.items.push(item)
    else list.push({ id: item.id, batch: item.batch ?? null, items: [item] })
    return list
  }, []))
  // A run of three or more rows by one reviewer starts folded.
  const RUN = 3
  const groups = $derived(groupRuns(rows))
  let open = $state(new Set())
  const toggle = id => { const next = new Set(open); next.has(id) ? next.delete(id) : next.add(id); open = next }
  const span = group => {
    const newest = group.items[0].at, oldest = group.items.at(-1).at
    try { return `${formatDateTime(oldest)} – ${formatDateTime(newest, { date: false })}` } catch { return oldest }
  }
  let undoing = $state('')
  async function undoBatch(batch) {
    undoing = batch; error = ''
    try { await request(`/atlas/corrections/${batch}/undo`, {}); await load() }
    catch (e) { error = e.message }
    finally { undoing = '' }
  }
  function when(at) { try { return formatDateTime(at) } catch { return at } }
  $effect(() => { if (nearEnd && hasMore && !error && !loading && !loadingMore) load(true) })
  onMount(() => { load(); return () => { closed = true; clearTimeout(filterTimer) } })
</script>

<!-- A row's character in its script's colour, or a note that it has none. -->
{#snippet label(text)}{#if text}<ScriptText {text} />{:else}{t('history.noLabel')}{/if}{/snippet}

{#snippet reviewer(item)}
  {@const who = item.reviewer ?? { name: item.actor, user: null, image: null, mine: false }}
  <span class="history-actor" class:mine={who.mine} class:unclaimed={!who.user} title={who.name}>
    <span class="avatar history-avatar" aria-hidden="true">{#if who.image}<img src={who.image} alt="" loading="lazy" referrerpolicy="no-referrer" />{:else}{initial(who.name)}{/if}</span>
    <span class="history-name">{who.name}{#if who.mine}<small>{t('history.reviewer.you')}</small>{/if}</span>
  </span>
{/snippet}

{#snippet entries(list)}
      {#each list as row (row.id)}
        {#if row.batch && row.items.length > 1}
          {@const item = row.items[0]}
          <li class="history-batch">
            <div class="history-row">
              <span class="history-time">{when(item.at)}</span>
              {@render reviewer(item)}
              <span class="history-label">{@render label(item.character ?? item.label)}</span>
              <span class="history-decision"><ScriptLine line={withText('history.batch', 'character', { count: row.items.length, character: item.character ?? item.label ?? '' })} />
                <span class="history-batch-labels">{#each row.items.map(entry => entry.label).filter(Boolean).slice(0, 12) as text, i (i)}{#if i}{' '}{/if}<ScriptText {text} />{/each}</span></span>
              {#if item.reviewer?.mine}<button class="quiet-link" disabled={undoing === row.batch} onclick={() => undoBatch(row.batch)}>{t('history.batch.undo')}</button>{/if}
            </div>
          </li>
        {:else}
          {#each row.items as item (item.id)}
            <li>
              {#if item.kind === 'passed'}
                <!-- A passed round names no single crop to open. -->
                <div class="history-row passed">
                  <span class="history-time">{when(item.at)}</span>
                  {@render reviewer(item)}
                  <span class="history-label">{@render label(item.label)}</span>
                  <span class="history-decision"><ScriptLine line={decisionText(item)} /></span>
                </div>
              {:else}
                <button class="history-row" class:undo={item.kind === 'undo'} onclick={() => inspect(item.target)}
                        aria-label={t('history.row.inspect', { label: item.label ?? item.target })}>
                  <span class="history-time">{when(item.at)}</span>
                  {@render reviewer(item)}
                  <span class="history-label">{@render label(item.label)}</span>
                  <span class="history-decision"><ScriptLine line={decisionText(item)} /></span>
                </button>
              {/if}
            </li>
          {/each}
        {/if}
      {/each}
{/snippet}

<section class="explore history">
  <h1 class="visually-hidden">{t('history.heading')}</h1>
  <div class="collection-toolbar">
    <div class="filter-tabs" aria-label={t('history.onlyMine.aria')}>
      <button class:active={onlyMine && !userFilter} aria-pressed={onlyMine && !userFilter} disabled={Boolean(userFilter)} onclick={toggleMine}>{t('history.onlyMine')}</button>
    </div>
    <a class="quiet-link history-ranking" href={localize('/ranking')}>{t('ranking.title')} ↗</a>
    <input class="history-character-filter" type="text" lang="ja" value={characterFilter}
           oninput={e => seekCharacter(e.currentTarget.value)}
           placeholder={t('history.characterFilter.placeholder')} aria-label={t('history.characterFilter.aria')} />
  </div>
  {#if userFilter}
    <div class="history-by-user">
      {#if items[0]?.reviewer?.user === userFilter}{@render reviewer(items[0])}{/if}
      <span>{t('history.byUser', { name: items.find(item => item.reviewer?.user === userFilter)?.reviewer.name ?? (loading ? '…' : userFilter.slice(0, 8)) })}</span>
      <a class="quiet-link" href={localize('/history')} onclick={event => { event.preventDefault(); clearUser() }}>{t('history.byUser.clear')}</a>
    </div>
  {/if}
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if loading && !items.length}
    <p class="find-count" role="status">{t('history.loading')}</p>
  {:else if !items.length}
    <div class="empty"><span class="empty-mark">∅</span><h2>{t('history.empty')}</h2></div>
  {:else}
    <ul class="history-list">
      {#each groups as group (group.id)}
        {#if group.items.length >= RUN}
          {@const first = group.items[0]}
          <li class="history-group" class:open={open.has(group.id)}>
            <button class="history-row history-group-head" aria-expanded={open.has(group.id)} onclick={() => toggle(group.id)}>
              <span class="history-time">{span(group)}</span>
              {@render reviewer(first)}
              <span class="history-group-count">{t('history.group.count', { count: group.items.length })}</span>
              <span class="history-decision history-group-labels">{#each group.items.map(entry => entry.character ?? entry.label).filter(Boolean).slice(0, 40) as text, i (i)}{#if i}{' '}{/if}<ScriptText {text} />{/each}</span>
              <span class="history-group-toggle" aria-hidden="true">{open.has(group.id) ? '−' : '+'}</span>
            </button>
            {#if open.has(group.id)}<ul class="history-group-rows">{@render entries(group.rows)}</ul>{/if}
          </li>
        {:else}{@render entries(group.rows)}{/if}
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
  .history-ranking { order: 2; margin-left: auto; white-space: nowrap; }
  .history-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
  .history-list li { border-bottom: 1px solid var(--line); }
  .history-row {
    display: flex; align-items: center; gap: 20px; width: 100%; border: 0; border-radius: 0;
    background: transparent; padding: 14px 6px; font-size: 13px; text-align: left;
  }
  .history-row:hover { background: var(--surface-tile); }
  .history-row.undo .history-decision { color: var(--wrong); }
  .history-row.passed .history-decision { color: var(--muted); }
  .history-batch-labels { margin-left: 10px; color: var(--muted); }
  .history-batch .quiet-link { flex: 0 0 auto; }
  .history-time { flex: 0 0 150px; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  .history-actor { flex: 0 0 150px; display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--ink-soft); font-size: 12px; }
  .history-actor.unclaimed { color: var(--muted); }
  .history-actor.unclaimed .history-avatar { background: var(--surface-badge); color: var(--muted); }
  .history-actor.mine .history-avatar { box-shadow: 0 0 0 2px var(--accent); }
  .history-avatar { width: 22px; height: 22px; font-size: 10px; }
  .history-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .history-name small { margin-left: 5px; font-size: 10px; color: var(--accent); }
  .history-by-user { display: flex; align-items: center; gap: 12px; padding: 12px 6px; font-size: 13px; }
  .history-by-user .history-actor { flex: 0 0 auto; }
  .history-by-user .history-name { display: none; }
  .history-label { flex: 0 0 40px; font-size: 20px; text-align: center; }
  .history-decision { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .history-group-head { font-weight: 500; }
  .history-group-count { flex: 0 0 auto; color: var(--ink); }
  .history-group-labels { color: var(--muted); font-weight: 400; }
  .history-group-toggle { flex: 0 0 18px; text-align: center; color: var(--muted); font-size: 16px; }
  .history-group.open > .history-group-head { background: var(--surface-tile); }
  .history-group-rows { list-style: none; margin: 0; padding: 0 0 0 18px; border-top: 1px solid var(--line); background: var(--surface-subtle); }
  .history-group-rows li:last-child { border-bottom: 0; }
  .load-more-row { display: flex; justify-content: center; padding: 22px 0; }
  @media (max-width: 700px) {
    .history-time { flex-basis: 90px; }
    .history-row { gap: 10px; }
    .history-actor { flex-basis: 22px; }
    .history-actor .history-name { display: none; }
  }
</style>
