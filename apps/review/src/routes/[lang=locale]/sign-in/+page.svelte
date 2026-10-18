<script>
  import { goto } from '$app/navigation'
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import SignIn from '$components/SignIn.svelte'
  import SpecimenWall from '$components/SpecimenWall.svelte'
  import { t, localize } from '$lib/i18n.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const session = useSession()
  // A sign-in reached from another page goes back there; otherwise to the account. Only a path on
  // this site is followed: a browser reads `//host` and `/\host` as another site.
  const next = $derived(sameSite(page.url.searchParams.get('next'), page.url.origin) ?? localize('/account'))
  function sameSite(path, origin) {
    if (!path?.startsWith('/')) return null
    const url = new URL(path, origin)
    // The address checked is the one followed; `/..//host` resolves to a path that starts `//`.
    return url.origin === origin && !url.pathname.startsWith('//') ? url.href : null
  }
  // A reader who is already signed in has nothing to do here.
  $effect(() => { if (session.state.user && !session.state.user.anonymous) goto(next, { replaceState: true }) })
</script>

<Seo title={t('signIn.title')} index={false} />

<section class="sign-in-page">
  <figure class="sign-in-wall"><SpecimenWall count={36} columns={6} /><figcaption class="overline">{t('signIn.wall')}</figcaption></figure>
  <div class="sign-in-card">
    <SignIn done={() => goto(next)} />
  </div>
</section>

<style>
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
