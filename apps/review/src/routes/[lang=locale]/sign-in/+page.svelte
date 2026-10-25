<script>
  import { goto } from '$app/navigation'
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import SignIn from '$components/SignIn.svelte'
  import SpecimenWall from '$components/SpecimenWall.svelte'
  import { t, localize } from '$lib/i18n.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  import { authClient } from '$lib/auth.js'
  const session = useSession()
  // A sign-in reached from another page goes back there; otherwise to the account. Only a path on
  // this site is followed: a browser reads `//host` and `/\host` as another site.
  const next = $derived(sameSite(page.url.searchParams.get('next'), page.url.origin) ?? localize('/account'))
  function sameSite(path, origin) {
    if (!path?.startsWith('/') || !URL.canParse(path, origin)) return null
    const url = new URL(path, origin)
    // The address checked is the one followed; `/..//host` resolves to a path that starts `//`.
    return url.origin === origin && !url.pathname.startsWith('//') ? url.href : null
  }
  // A sign-in the Matrix homeserver asked for carries the provider's signed request (`sig`). Signing in
  // then sends the browser back to the homeserver, and a reader already signed in is here because the
  // homeserver asked them to sign in again, so the page stays.
  const forMatrix = $derived(page.url.searchParams.has('sig'))
  // The homeserver's "Switch accounts" asks which account to use (`prompt=select_account`). A reader who
  // is signed in goes on with that account, or signs out and signs in as another, which then goes on.
  const choosing = $derived(forMatrix && (page.url.searchParams.get('prompt') ?? '').split(' ').includes('select_account')
    && session.state.user && !session.state.user.anonymous)
  let busy = $state(false), failed = $state(false)
  async function proceed() {
    busy = true; failed = false
    try {
      const { data, error } = await (await authClient()).oauth2.continue({ selected: true })
      if (error || !data?.url) throw error
      location.assign(data.url)
    } catch { failed = true; busy = false }
  }
  async function another() {
    busy = true; failed = false
    try { await session.signOut() } catch { failed = true }
    busy = false
  }
  // A reader who is already signed in has nothing to do here.
  $effect(() => { if (!forMatrix && session.state.user && !session.state.user.anonymous) goto(next, { replaceState: true }) })
</script>

<Seo title={t('signIn.title')} index={false} />

<section class="sign-in-page">
  <figure class="sign-in-wall"><SpecimenWall count={36} columns={6} /><figcaption class="overline">{t('signIn.wall')}</figcaption></figure>
  <div class="sign-in-card">
    {#if choosing}
      <div class="choose">
        <div class="choose-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg></div>
        <h1>{t('signIn.choose.title')}</h1>
        <button class="primary" onclick={proceed} disabled={busy}>{t('signIn.choose.continue', { name: session.state.user.name })}</button>
        <button class="quiet-link" onclick={another} disabled={busy}>{t('signIn.choose.other')}</button>
        {#if failed}<p class="choose-error" role="alert">{t('signIn.error.other')}</p>{/if}
      </div>
    {:else}
      <SignIn done={() => { if (!forMatrix) goto(next) }} />
    {/if}
  </div>
</section>

<style>
  .choose { display: grid; gap: 14px; }
  /* The mark and title as the sign-in form draws them (`SignIn.svelte`). */
  .choose-mark { width: 44px; height: 44px; border-radius: 11px; display: grid; place-items: center; color: var(--on-color); margin-bottom: 8px;
    background: linear-gradient(135deg, var(--accent-solid), light-dark(#9a8cff, #7a6cf0)); box-shadow: 0 8px 24px var(--accent-wash); }
  .choose-mark svg { width: 22px; height: 22px; }
  .choose h1 { font-size: 26px; font-weight: 500; letter-spacing: -.6px; margin: 0 0 8px; text-wrap: balance; }
  .choose .primary { overflow-wrap: anywhere; }
  .choose-error { color: var(--wrong); font-size: 13px; }
  .sign-in-page { min-height: calc(100dvh - 88px - 70px); display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(360px, 440px); gap: 6vw;
    align-items: center; max-width: 1240px; margin: auto; padding: 48px 4.4vw; }
  .sign-in-wall { min-width: 0; margin: 0; display: grid; gap: 14px; justify-items: center; }
  .sign-in-card { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 36px 34px 28px;
    box-shadow: 0 24px 70px var(--shadow-soft); }
  @media (max-width: 900px) {
    .sign-in-page { grid-template-columns: minmax(0, 1fr); gap: 0; padding: 0 20px 32px; align-items: start; }
    .sign-in-wall { height: 150px; overflow: hidden; margin: 0 -20px; }
    .sign-in-wall figcaption { display: none; }
    .sign-in-card { margin-top: -48px; position: relative; padding: 28px 22px 22px; }
  }
</style>
