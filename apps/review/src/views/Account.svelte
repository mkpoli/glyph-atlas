<script>
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { authClient, passkeys } from '../lib/auth.js'
  import { useSession } from '../lib/session.svelte.js'
  import { t, formatDateTime, localize } from '../lib/i18n.svelte.js'
  import ProviderIcon from '$components/ProviderIcon.svelte'
  import SignIn from '$components/SignIn.svelte'
  const session = useSession()
  const NAMES = { github: 'GitHub', google: 'Google', discord: 'Discord', line: 'LINE', kakao: 'Kakao' }
  let name = $state(''), accounts = $state([]), keys = $state([]), sessions = $state([]), current = $state('')
  let loaded = $state(false), busy = $state(''), error = $state(''), notice = $state(''), canPasskey = $state(false), email = $state('')
  const user = $derived(session.state.user)
  const linked = $derived(new Set(accounts.map(account => account.providerId)))
  // An account keeps one way in: the last sign-in method cannot be removed. Its address takes a code.
  const methods = $derived(accounts.length + keys.length + (email && !email.endsWith('.invalid') ? 1 : 0))

  async function load() {
    const client = await authClient()
    const [mine, listed, open, now] = await Promise.all([client.listAccounts(), client.passkey.listUserPasskeys(), client.listSessions(), client.getSession()])
    accounts = (mine.data ?? []).filter(account => account.providerId !== 'credential')
    keys = listed.data ?? []
    sessions = (open.data ?? []).sort((a, b) => (b.updatedAt > a.updatedAt ? 1 : -1))
    current = now.data?.session?.token ?? ''
    email = now.data?.user?.email ?? ''
    loaded = true
  }
  async function act(key, work, done = '') {
    busy = key; error = ''; notice = ''
    try {
      const result = await work(await authClient())
      if (result?.error) throw result.error
      notice = done
      await load()
    } catch (failure) { if (failure?.code !== 'AUTH_CANCELLED') error = failure?.message || t('signIn.error.other') }
    finally { busy = '' }
  }
  const rename = event => { event.preventDefault(); return act('name', async client => { const r = await client.updateUser({ name: name.trim() }); await session.refresh(); return r }, t('account.name.saved')) }
  const link = provider => act(provider, client => client.linkSocial({ provider, callbackURL: location.pathname }))
  const unlink = provider => act(provider, client => client.unlinkAccount({ providerId: provider }))
  const addKey = () => act('add', client => client.passkey.addPasskey({ name: navigator.platform || undefined }), t('account.passkeys.added'))
  const dropKey = id => act(id, client => client.passkey.deletePasskey({ id }))
  const revoke = token => act(token, client => client.revokeSession({ token }))
  const revokeOthers = () => act('others', client => client.revokeOtherSessions())
  async function signOut() { await session.signOut(); goto(localize('/')) }
  // A browser and its system, read from the session's user agent, which is all a session records of its device.
  function device(agent = '') {
    const browser = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox' : /Chrome\//.test(agent) ? 'Chrome' : /Safari\//.test(agent) ? 'Safari' : ''
    const system = /iPhone|iPad/.test(agent) ? 'iOS' : /Android/.test(agent) ? 'Android' : /Mac OS X/.test(agent) ? 'macOS' : /Windows/.test(agent) ? 'Windows' : /Linux/.test(agent) ? 'Linux' : ''
    return [browser, system].filter(Boolean).join(' · ') || t('account.sessions.unknown')
  }
  const when = at => { try { return formatDateTime(at) } catch { return String(at) } }
  $effect(() => { if (user && !user.anonymous) name = user.name })
  onMount(async () => {
    canPasskey = (await passkeys()).supported
    if (user && !user.anonymous) await load().catch(failure => error = failure.message)
  })
</script>

<section class="account">
  {#if !user || user.anonymous}
    <div class="account-card account-guest"><SignIn done={() => { load(); }} /></div>
  {:else}
    <header class="account-head">
      <span class="avatar large" aria-hidden="true">{#if session.state.user.image}<img src={session.state.user.image} alt="" />{:else}{user.name.slice(0, 1).toUpperCase()}{/if}</span>
      <div><p class="overline">{t('account.overline')}</p><h1>{user.name}</h1>{#if email && !email.endsWith('.invalid')}<p class="account-email">{email}</p>{/if}</div>
      <button class="account-sign-out" onclick={signOut}>{t('account.signOut')}</button>
    </header>
    {#if error}<p class="error-message" role="alert">{error}</p>{/if}
    {#if notice}<p class="replaced-note" role="status">{notice}</p>{/if}
    {#if !loaded}<div class="account-card"><div class="account-loading shimmer"></div></div>{:else}

    <div class="account-card">
      <h2>{t('account.name.title')}</h2>
      <p class="account-hint">{t('account.name.hint')}</p>
      <form class="account-row-form" onsubmit={rename}>
        <input bind:value={name} maxlength="64" aria-label={t('account.name.title')} />
        <button class="primary" disabled={busy === 'name' || !name.trim() || name.trim() === user.name}>{t('account.name.save')}</button>
      </form>
    </div>

    <div class="account-card">
      <h2>{t('account.methods.title')}</h2>
      <ul class="account-list">
        {#if email && !email.endsWith('.invalid')}<li><span class="method-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg></span><span>{t('account.methods.email')}<small>{email}</small></span></li>{/if}
        {#each session.state.providers as provider (provider)}
          <li><span class="method-icon"><ProviderIcon name={provider} /></span><span>{NAMES[provider]}</span>
            {#if linked.has(provider)}<button class="quiet-link" disabled={methods < 2 || busy === provider} onclick={() => unlink(provider)}>{t('account.methods.unlink')}</button>
            {:else}<button disabled={busy === provider} onclick={() => link(provider)}>{t('account.methods.link')}</button>{/if}</li>
        {/each}
      </ul>
    </div>

    <div class="account-card">
      <div class="account-card-head"><h2>{t('account.passkeys.title')}</h2>{#if canPasskey}<button disabled={busy === 'add'} onclick={addKey}>{t('account.passkeys.add')}</button>{/if}</div>
      <p class="account-hint">{t('account.passkeys.hint')}</p>
      {#if keys.length}
        <ul class="account-list">
          {#each keys as key (key.id)}
            <li><span class="method-icon"><ProviderIcon name="passkey" /></span><span>{key.name || t('account.passkeys.unnamed')}<small>{t('account.passkeys.created', { date: when(key.createdAt) })}{key.backedUp ? ' · ' + t('account.passkeys.synced') : ''}</small></span>
              <button class="quiet-link" disabled={busy === key.id} onclick={() => dropKey(key.id)}>{t('account.remove')}</button></li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="account-card">
      <div class="account-card-head"><h2>{t('account.sessions.title')}</h2>{#if sessions.length > 1}<button class="quiet-link" disabled={busy === 'others'} onclick={revokeOthers}>{t('account.sessions.revokeOthers')}</button>{/if}</div>
      <ul class="account-list">
        {#each sessions as open (open.id)}
          <li><span class="session-dot" class:here={open.token === current}></span><span>{device(open.userAgent)}<small>{open.token === current ? t('account.sessions.here') : t('account.sessions.active', { date: when(open.updatedAt) })}</small></span>
            {#if open.token !== current}<button class="quiet-link" disabled={busy === open.token} onclick={() => revoke(open.token)}>{t('account.sessions.revoke')}</button>{/if}</li>
        {/each}
      </ul>
    </div>
    {/if}
  {/if}
</section>

<style>
  .account { max-width: 680px; margin: auto; padding: 40px 20px 60px; display: grid; gap: 16px; }
  .account-head { display: flex; align-items: center; gap: 18px; margin-bottom: 10px; }
  .account-head h1 { font-size: 28px; font-weight: 500; letter-spacing: -.6px; margin-top: 4px; }
  .account-email { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .account-sign-out { margin-left: auto; font-size: 12px; white-space: nowrap; }
  .account-head > div { min-width: 0; }
  .account-head h1 { overflow-wrap: anywhere; }
  @media (max-width: 700px) { .account-head { gap: 14px; } .account-head h1 { font-size: 22px; } .avatar.large { width: 44px; height: 44px; } }
  .account-card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 22px 24px; }
  .account-guest { max-width: 440px; width: 100%; margin: 20px auto; padding: 34px 32px 26px; }
  .account-card h2 { font-size: 15px; font-weight: 600; }
  .account-card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .account-card-head button { font-size: 12px; padding: 8px 12px; }
  .account-hint { font-size: 12px; line-height: 1.5; color: var(--muted); margin: 6px 0 14px; }
  .account-row-form { display: flex; gap: 8px; }
  .account-row-form input { flex: 1; font-size: 14px; padding: 10px 12px; }
  .account-row-form .primary { gap: 0; padding: 10px 16px; }
  .account-list { list-style: none; margin: 12px 0 0; padding: 0; }
  .account-list li { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line); font-size: 13px; }
  .account-list li:first-child { border-top: 0; }
  .account-list li > span:nth-child(2) { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .account-list small { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; }
  .account-list button:not(.quiet-link) { font-size: 12px; padding: 7px 12px; }
  .method-icon { width: 18px; display: grid; place-items: center; flex-shrink: 0; }
  .account-loading { height: 220px; border-radius: 8px; }
  .session-dot { width: 8px; height: 8px; margin: 0 5px; border-radius: 50%; background: var(--line-strong); flex-shrink: 0; }
  .session-dot.here { background: var(--good); box-shadow: 0 0 0 4px var(--good-light); }
</style>
