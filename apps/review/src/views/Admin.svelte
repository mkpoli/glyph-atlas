<script>
  import { onMount } from 'svelte'
  import { authClient } from '../lib/auth.js'
  import { request } from '../lib/client.js'
  import { useSession } from '../lib/session.svelte.js'
  import { t, formatDateTime, localize, formatNumber } from '../lib/i18n.svelte.js'
  const session = useSession()
  const KINDS = ['active', 'accounts', 'anonymous', 'banned', 'admins', 'unclaimed']
  let kind = $state('active'), find = $state(''), people = $state([]), more = $state(null), loading = $state(false)
  let chosen = $state(null), work = $state([]), workMore = $state(null), busy = $state(''), error = $state(''), notice = $state('')
  let confirming = $state(null), reason = $state(''), banReason = $state('')
  let timer
  const json = path => fetch(path).then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.detail); return value })
  const who = person => person.user ? { user: person.id } : { actor: person.actor }

  async function list(append = false) {
    loading = true; error = ''
    try {
      const value = await json('/api/admin/reviewers?' + new URLSearchParams({ kind, q: find.trim(), offset: append ? more : 0 }))
      people = append ? [...people, ...value.items] : value.items
      more = value.next
    } catch (failure) { error = failure.message } finally { loading = false }
  }
  async function open(person, append = false) {
    if (!append) { chosen = { ...person, user: Boolean(person.id) }; work = []; confirming = null }
    try {
      const value = await json('/api/admin/submissions?' + new URLSearchParams({ ...who(chosen), offset: append ? workMore : 0 }))
      work = append ? [...work, ...value.items] : value.items
      workMore = value.next
    } catch (failure) { error = failure.message }
  }
  // A rejection goes to the reviewer and reason that were confirmed, a page at a time; the list is
  // held still until it ends.
  async function rejecting(target, body) {
    busy = 'reject'; error = ''; notice = ''
    const done = { rejected: 0, conflicts: 0, unavailable: 0 }
    try {
      let page = body
      while (page) {
        const result = await request('/api/admin/reject', page)
        done.rejected += result.rejected; done.conflicts += result.conflicts.length; done.unavailable += result.unavailable.length
        notice = summary(done)
        page = result.next ? { ...body, next: result.next } : result.left.length ? { ...body, submissions: result.left } : null
      }
      await Promise.all([open(target), list()])
    } catch (failure) { error = failure.message } finally { busy = ''; confirming = null }
  }
  const rejectSome = keys => rejecting(chosen, { submissions: keys, reason: reason.trim() })
  const rejectAll = () => rejecting(chosen, { ...who(chosen), reason: reason.trim() })
  const summary = ({ rejected, conflicts, unavailable }) => [t('admin.rejected', { count: rejected }),
    conflicts ? t('admin.conflicts', { count: conflicts }) : '', unavailable ? t('admin.unavailable', { count: unavailable }) : ''].filter(Boolean).join(' ')
  async function account(action) {
    busy = action; error = ''; notice = ''
    try {
      const client = await authClient()
      const result = action === 'ban' ? await client.admin.banUser({ userId: chosen.id, banReason: banReason.trim() || undefined })
        : action === 'unban' ? await client.admin.unbanUser({ userId: chosen.id })
        : await client.admin.setRole({ userId: chosen.id, role: action === 'promote' ? 'admin' : 'user' })
      if (result.error) throw new Error(result.error.message)
      chosen = { ...chosen, banned: action === 'ban' ? true : action === 'unban' ? false : chosen.banned,
        role: action === 'promote' ? 'admin' : action === 'demote' ? 'user' : chosen.role }
      confirming = null; await list()
    } catch (failure) { error = failure.message } finally { busy = '' }
  }
  function search(value) { find = value; clearTimeout(timer); timer = setTimeout(() => list(), 250) }
  const when = at => { try { return formatDateTime(at) } catch { return at ?? '' } }
  const verdicts = item => Object.entries(item.verdicts ?? {}).map(([verdict, n]) => `${t(`admin.verdict.${verdict}`)} ${formatNumber(n)}`).join(' · ')
  const standing = $derived(work.filter(item => item.state === 'standing').length)
  onMount(() => { list(); return () => clearTimeout(timer) })
</script>

<section class="admin">
  <header class="admin-head"><p class="overline">{t('admin.overline')}</p><h1>{t('admin.title')}</h1></header>
  {#if error}<p class="error-message" role="alert">{error}</p>{/if}
  {#if notice}<p class="replaced-note" role="status">{notice}</p>{/if}
  <div class="admin-layout">
    <aside class="admin-people">
      <div class="filter-tabs admin-kinds">{#each KINDS as value (value)}<button class:active={kind === value} aria-pressed={kind === value} onclick={() => { kind = value; list() }}>{t(`admin.kind.${value}`)}</button>{/each}</div>
      <input class="admin-find" type="search" value={find} oninput={event => search(event.currentTarget.value)} placeholder={t('admin.find')} aria-label={t('admin.find')} />
      <ul>
        {#each people as person (person.id ?? person.actor)}
          <li><button class="admin-person" disabled={Boolean(busy)} class:chosen={chosen && (chosen.id ?? chosen.actor) === (person.id ?? person.actor)} onclick={() => open(person)}>
            <span class="avatar">{#if person.image}<img src={person.image} alt="" />{:else}{(person.name || '?').slice(0, 1).toUpperCase()}{/if}</span>
            <span class="admin-person-text"><strong>{person.name}</strong>
              <small>{person.email ?? (person.actor ? t('admin.unclaimed') : person.anonymous ? t('admin.anonymous') : '')}</small>
              <small>{t('admin.saved', { count: person.submissions ?? 0 })}{person.rejected ? ' · ' + t('admin.rejectedCount', { count: person.rejected }) : ''}{person.last ? ' · ' + when(person.last) : ''}</small></span>
            {#if person.role === 'admin'}<span class="admin-pill">{t('admin.role.admin')}</span>{/if}
            {#if person.banned}<span class="admin-pill danger">{t('admin.banned')}</span>{/if}
          </button></li>
        {:else}<li class="admin-empty">{loading ? t('history.loading') : t('admin.nobody')}</li>{/each}
      </ul>
      {#if more}<button class="quiet-link admin-more" onclick={() => list(true)}>{t('admin.more')}</button>{/if}
    </aside>

    <div class="admin-detail">
      {#if !chosen}<div class="empty"><span class="empty-mark">←</span><h2>{t('admin.choose')}</h2></div>
      {:else}
        <div class="admin-card">
          <div class="admin-who">
            <span class="avatar large">{#if chosen.image}<img src={chosen.image} alt="" />{:else}{(chosen.name || '?').slice(0, 1).toUpperCase()}{/if}</span>
            <div><h2>{chosen.name}</h2><p>{chosen.email ?? (chosen.actor ? t('admin.unclaimed') : t('admin.anonymous'))}{chosen.lastLoginMethod ? ' · ' + t('admin.lastMethod', { method: chosen.lastLoginMethod }) : ''}</p></div>
            <a class="quiet-link" href={localize('/history') + '?' + new URLSearchParams(chosen.user ? { user: chosen.id } : {})}>{t('nav.history')} ↗</a>
          </div>
          <div class="admin-actions">
            <button class="negative" disabled={!standing || Boolean(busy)} onclick={() => { confirming = 'all'; reason = '' }}>{t('admin.rejectAll')}</button>
            {#if chosen.user && chosen.id !== session.state.user?.id}
              {#if chosen.banned}<button disabled={Boolean(busy)} onclick={() => account('unban')}>{t('admin.unban')}</button>
              {:else}<button disabled={Boolean(busy)} onclick={() => { confirming = 'ban'; banReason = '' }}>{t('admin.ban')}</button>{/if}
              {#if !chosen.anonymous}{#if chosen.role === 'admin'}<button disabled={Boolean(busy)} onclick={() => account('demote')}>{t('admin.demote')}</button>
              {:else}<button disabled={Boolean(busy)} onclick={() => account('promote')}>{t('admin.promote')}</button>{/if}{/if}
            {/if}
          </div>
          {#if confirming === 'all' || (confirming && confirming !== 'ban')}
            <form class="admin-confirm" onsubmit={event => { event.preventDefault(); confirming === 'all' ? rejectAll() : rejectSome([confirming]) }}>
              <p>{confirming === 'all' ? t('admin.rejectAll.confirm', { count: standing, name: chosen.name }) : t('admin.reject.confirm')}</p>
              <input bind:value={reason} maxlength="500" placeholder={t('admin.reason')} aria-label={t('admin.reason')} />
              <div><button type="button" onclick={() => confirming = null}>{t('admin.cancel')}</button><button class="negative" disabled={busy === 'reject'}>{busy === 'reject' ? t('admin.rejecting') : t('admin.reject')}</button></div>
            </form>
          {:else if confirming === 'ban'}
            <form class="admin-confirm" onsubmit={event => { event.preventDefault(); account('ban') }}>
              <p>{t('admin.ban.confirm', { name: chosen.name })}</p>
              <input bind:value={banReason} maxlength="200" placeholder={t('admin.reason')} aria-label={t('admin.reason')} />
              <div><button type="button" onclick={() => confirming = null}>{t('admin.cancel')}</button><button class="negative" disabled={busy === 'ban'}>{t('admin.ban')}</button></div>
            </form>
          {/if}
        </div>

        <ul class="admin-work">
          {#each work as item (item.id)}
            <li class:gone={item.state !== 'standing'}>
              <span class="admin-time">{when(item.at)}</span>
              <span class="admin-label" lang="ja">{item.label ?? '—'}</span>
              <span class="admin-what">{t(`admin.submission.${item.kind}`, { count: item.crops })}<small>{verdicts(item)}</small></span>
              {#if item.state === 'standing'}<button class="quiet-link" disabled={Boolean(busy)} onclick={() => { confirming = item.id; reason = '' }}>{t('admin.reject')}</button>
              {:else}<span class="admin-pill" class:danger={item.state === 'rejected'} title={item.rejection ? `${item.rejection.by}${item.rejection.reason ? ': ' + item.rejection.reason : ''}` : ''}>{t(`admin.state.${item.state}`)}</span>{/if}
            </li>
          {:else}<li class="admin-empty">{t('admin.noWork')}</li>{/each}
        </ul>
        {#if workMore}<button class="quiet-link admin-more" onclick={() => open(chosen, true)}>{t('admin.more')}</button>{/if}
      {/if}
    </div>
  </div>
</section>

<style>
  .admin { max-width: 1280px; margin: auto; padding: 28px 4.4vw 60px; }
  .admin-head h1 { font-size: 28px; font-weight: 500; letter-spacing: -.6px; margin: 6px 0 20px; }
  .admin-layout { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: 24px; align-items: start; }
  .admin-people { position: sticky; top: 16px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
  .admin-kinds { flex-wrap: wrap; margin-bottom: 10px; }
  .admin-kinds button { padding: 6px 10px; font-size: 11px; }
  .admin-find { width: 100%; font-size: 13px; padding: 9px 11px; margin-bottom: 8px; }
  .admin-people ul, .admin-work { list-style: none; margin: 0; padding: 0; }
  .admin-people ul { max-height: calc(100dvh - 260px); overflow: auto; }
  .admin-person { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; border: 0; border-radius: 8px; padding: 9px 8px; background: transparent; }
  .admin-person:hover, .admin-person.chosen { background: var(--accent-hover); }
  .admin-person.chosen { box-shadow: inset 2px 0 0 var(--accent); }
  .admin-person-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .admin-person-text strong { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .admin-person-text small { font-size: 10px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .admin-pill { flex-shrink: 0; font-size: 9px; letter-spacing: .3px; padding: 3px 7px; border-radius: 20px; color: var(--accent); background: var(--accent-light); }
  .admin-pill.danger { color: var(--wrong); background: var(--wrong-light); }
  .admin-empty { font-size: 12px; color: var(--muted); padding: 18px 8px; text-align: center; }
  .admin-more { display: block; margin: 12px auto 0; }
  .admin-card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px; margin-bottom: 14px; }
  .admin-who { display: flex; align-items: center; gap: 14px; }
  .admin-who > div { flex: 1; min-width: 0; }
  .admin-who h2 { font-size: 20px; font-weight: 500; overflow-wrap: anywhere; }
  .admin-who p { font-size: 12px; color: var(--muted); margin-top: 3px; overflow-wrap: anywhere; }
  .admin-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .admin-actions button { font-size: 12px; padding: 9px 13px; }
  .admin-confirm { margin-top: 14px; padding: 14px; border-radius: 9px; background: var(--wrong-light); display: grid; gap: 10px; animation: open .18s ease; }
  .admin-confirm p { font-size: 13px; line-height: 1.5; }
  .admin-confirm input { font-size: 13px; padding: 9px 11px; }
  .admin-confirm div { display: flex; justify-content: flex-end; gap: 8px; }
  .admin-confirm button { font-size: 12px; padding: 8px 13px; }
  @keyframes open { from { opacity: 0; transform: translateY(-4px) } }
  .admin-work { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .admin-work li { display: flex; align-items: center; gap: 14px; padding: 11px 18px; border-top: 1px solid var(--line); font-size: 12px; }
  .admin-work li:first-child { border-top: 0; }
  .admin-work li.gone { color: var(--muted); }
  .admin-work li.gone .admin-label { text-decoration: line-through; text-decoration-color: var(--wrong); }
  .admin-time { flex: 0 0 150px; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  .admin-label { flex: 0 0 34px; font-size: 20px; text-align: center; }
  .admin-what { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .admin-what small { font-size: 10px; color: var(--muted); }
  @media (max-width: 900px) {
    .admin-layout { grid-template-columns: minmax(0, 1fr); }
    .admin-people { position: static; }
    .admin-people ul { max-height: 320px; }
    .admin-time { flex-basis: 92px; font-size: 10px; }
    .admin-work li { gap: 10px; padding: 10px 12px; }
  }
</style>
