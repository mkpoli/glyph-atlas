<script>
  import { onMount, tick } from 'svelte'
  import { authClient, passkeys } from '../lib/auth.js'
  import { useSession } from '../lib/session.svelte.js'
  import { t } from '../lib/i18n.svelte.js'
  import ProviderIcon from './ProviderIcon.svelte'
  // `done` runs once the reader is signed in; `skip` closes the form without signing in.
  let { done = () => {}, skip = null, heading = 'h1' } = $props()
  const session = useSession()
  const NAMES = { github: 'GitHub', google: 'Google', discord: 'Discord', line: 'LINE', kakao: 'Kakao' }
  const COOLDOWN = 30
  let stage = $state('start'), email = $state(''), code = $state(''), busy = $state(''), error = $state('')
  let last = $state(null), passkey = $state({ supported: false, autofill: false }), wait = $state(0)
  let codeInput = $state(), timer
  const carried = $derived(session.state.user?.anonymous ? session.state.user.name : null)

  function problem(failure) {
    if (failure?.status === 429) return t('signIn.error.tooMany')
    if (failure?.code === 'INVALID_OTP') return t('signIn.error.code')
    if (failure?.code === 'OTP_EXPIRED') return t('signIn.error.expired')
    if (failure?.code === 'TOO_MANY_ATTEMPTS') return t('signIn.error.attempts')
    return failure?.message || t('signIn.error.other')
  }
  async function finish(offerPasskey = false) {
    await session.refresh()
    if (offerPasskey && passkey.supported) { stage = 'passkey'; return }
    done()
  }
  async function withPasskey() {
    busy = 'passkey'; error = ''
    const { error: failure } = await (await authClient()).signIn.passkey()
    busy = ''
    if (failure) { if (failure.code !== 'AUTH_CANCELLED') error = problem(failure); return }
    await finish()
  }
  async function withProvider(provider) {
    busy = provider; error = ''
    const { error: failure } = await (await authClient()).signIn.social({ provider, callbackURL: location.pathname + location.search })
    if (failure) { busy = ''; error = problem(failure) }
  }
  function countdown() {
    wait = COOLDOWN; clearInterval(timer)
    timer = setInterval(() => { if (--wait <= 0) clearInterval(timer) }, 1000)
  }
  async function sendCode(event) {
    event?.preventDefault()
    if (!email.trim() || busy) return
    busy = 'send'; error = ''
    const { error: failure } = await (await authClient()).emailOtp.sendVerificationOtp({ email: email.trim(), type: 'sign-in' })
    busy = ''
    if (failure) { error = problem(failure); return }
    stage = 'code'; code = ''; countdown()
    await tick(); codeInput?.focus()
  }
  async function verify() {
    if (code.length !== 6 || busy) return
    busy = 'verify'; error = ''
    const { error: failure } = await (await authClient()).signIn.emailOtp({ email: email.trim(), otp: code })
    busy = ''
    if (failure) { error = problem(failure); code = ''; codeInput?.focus(); return }
    await finish(true)
  }
  function typed(event) {
    code = event.currentTarget.value.replace(/\D/g, '').slice(0, 6)
    event.currentTarget.value = code
    if (code.length === 6) verify()
  }
  async function addPasskey() {
    busy = 'add'; error = ''
    const { error: failure } = await (await authClient()).passkey.addPasskey({ name: navigator.platform || undefined })
    busy = ''
    if (failure && failure.code !== 'AUTH_CANCELLED') { error = problem(failure); return }
    done()
  }
  onMount(() => {
    let closed = false
    // What the browser can tell at once is shown at once; the accounts client loads behind it.
    passkey = { supported: typeof PublicKeyCredential !== 'undefined', autofill: false }
    last = document.cookie.match(/(?:^|;\s*)better-auth\.last_used_login_method=([^;]+)/)?.[1] ?? null
    ;(async () => {
      const client = await authClient()
      last = client.getLastUsedLoginMethod()
      passkey = await passkeys()
      // A passkey saved for this site is offered from the address field, without a button.
      if (passkey.autofill && !closed) {
        const { data } = await client.signIn.passkey({ autoFill: true })
        if (data && !closed) await finish()
      }
    })()
    return () => { closed = true; clearInterval(timer) }
  })
</script>

<div class="sign-in">
  <div class="sign-in-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg></div>
  {#if stage === 'passkey'}
    <svelte:element this={heading} class="sign-in-title">{t('signIn.passkeyOffer.title')}</svelte:element>
    <p class="sign-in-lede">{t('signIn.passkeyOffer.body')}</p>
    {#if error}<p class="sign-in-error" role="alert">{error}</p>{/if}
    <button class="primary sign-in-wide" disabled={busy === 'add'} onclick={addPasskey}><span class="method-icon"><ProviderIcon name="passkey" /></span>{t('signIn.passkeyOffer.add')}</button>
    <button class="quiet-link sign-in-skip" onclick={() => done()}>{t('signIn.passkeyOffer.later')}</button>
  {:else}
    <svelte:element this={heading} class="sign-in-title">{t('signIn.title')}</svelte:element>
    <p class="sign-in-lede">{carried ? t('signIn.lede.carried', { name: carried }) : t('signIn.lede')}</p>
    {#if error}<p class="sign-in-error" role="alert">{error}</p>{/if}

    {#if stage === 'start'}
      <div class="sign-in-methods">
        {#if passkey.supported}
          <button class="method method-passkey" disabled={Boolean(busy)} onclick={withPasskey}>
            <span class="method-icon"><ProviderIcon name="passkey" /></span>{t('signIn.passkey')}
            {#if last === 'passkey'}<span class="last-used">{t('signIn.lastUsed')}</span>{/if}
          </button>
        {/if}
        {#each session.state.providers as provider (provider)}
          <button class="method" disabled={Boolean(busy)} aria-busy={busy === provider} onclick={() => withProvider(provider)}>
            <span class="method-icon"><ProviderIcon name={provider} /></span>{t('signIn.provider', { name: NAMES[provider] })}
            {#if last === provider}<span class="last-used">{t('signIn.lastUsed')}</span>{/if}
          </button>
        {/each}
      </div>
      <div class="sign-in-divider"><span>{t('signIn.orEmail')}</span></div>
      <form class="sign-in-email" onsubmit={sendCode}>
        <label class="visually-hidden" for="sign-in-email">{t('signIn.email.label')}</label>
        <input id="sign-in-email" type="email" required bind:value={email} placeholder={t('signIn.email.placeholder')}
               autocomplete={passkey.autofill ? 'username webauthn' : 'email'} inputmode="email" spellcheck="false" />
        <button class="primary" disabled={busy === 'send' || !email.trim()}>{busy === 'send' ? t('signIn.sending') : t('signIn.sendCode')}
          {#if last === 'email-otp'}<span class="last-used on-color">{t('signIn.lastUsed')}</span>{/if}</button>
      </form>
    {:else}
      <p class="sign-in-sent">{t('signIn.sent', { email: email.trim() })}</p>
      <label class="otp" class:busy={busy === 'verify'}>
        <span class="visually-hidden">{t('signIn.code.label')}</span>
        <input bind:this={codeInput} value={code} oninput={typed} inputmode="numeric" autocomplete="one-time-code"
               pattern="[0-9]*" maxlength="6" disabled={busy === 'verify'} />
        <span class="otp-cells" aria-hidden="true">{#each Array(6) as _, i (i)}<span class:filled={code[i]} class:caret={i === code.length}>{code[i] ?? ''}</span>{/each}</span>
      </label>
      <div class="sign-in-code-actions">
        <button class="quiet-link" onclick={() => { stage = 'start'; error = '' }}>{t('signIn.otherEmail')}</button>
        <button class="quiet-link" disabled={wait > 0 || Boolean(busy)} onclick={() => sendCode()}>{wait > 0 ? t('signIn.resendIn', { seconds: wait }) : t('signIn.resend')}</button>
      </div>
    {/if}

    <p class="sign-in-terms">{t('signIn.terms')} <a href="https://github.com/mkpoli/glyph-atlas/blob/main/CONTRIBUTING.md" rel="noopener" target="_blank">CONTRIBUTING ↗</a></p>
    {#if skip}<button class="quiet-link sign-in-skip" onclick={skip}>{t('signIn.skip')}</button>{/if}
  {/if}
</div>

<style>
  .sign-in { display: flex; flex-direction: column; gap: 0; width: 100%; }
  .sign-in-mark { width: 44px; height: 44px; border-radius: 11px; display: grid; place-items: center; color: var(--on-color);
    background: linear-gradient(135deg, var(--accent-solid), light-dark(#9a8cff, #7a6cf0)); box-shadow: 0 8px 24px var(--accent-wash); margin-bottom: 22px; }
  .sign-in-mark svg { width: 22px; height: 22px; }
  .sign-in-title { font-size: 26px; font-weight: 500; letter-spacing: -.6px; margin: 0 0 8px; }
  .sign-in-lede { font-size: 13px; line-height: 1.55; color: var(--muted); margin-bottom: 24px; }
  .sign-in-error { font-size: 12px; color: light-dark(#9d3540, #ff929f); background: var(--wrong-light); padding: 10px 12px; border-radius: 7px; margin-bottom: 14px; }
  .sign-in-methods { display: grid; gap: 8px; }
  .method { position: relative; display: flex; align-items: center; gap: 12px; width: 100%; font-size: 13px; padding: 12px 14px; text-align: left;
    background: var(--surface); transition: border-color .15s, background .15s, transform .15s; }
  .method:not(:disabled):hover { background: var(--accent-hover); border-color: var(--line-hover); }
  .method:not(:disabled):active { transform: scale(.99); }
  .method-passkey { background: var(--action-surface); color: var(--on-color); border-color: var(--action-surface); }
  .method-passkey:not(:disabled):hover { background: var(--accent-solid); border-color: var(--accent-solid); }
  .method-icon { width: 18px; height: 18px; display: grid; place-items: center; flex-shrink: 0; }
  .last-used { margin-left: auto; font-size: 10px; letter-spacing: .3px; color: var(--accent); background: var(--accent-light); padding: 3px 7px; border-radius: 20px; }
  .method-passkey .last-used, .last-used.on-color { color: var(--on-color); background: rgb(255 255 255 / 18%); margin-left: 10px; }
  .sign-in-divider { display: flex; align-items: center; gap: 12px; margin: 20px 0 14px; font-size: 11px; color: var(--muted); }
  .sign-in-divider::before, .sign-in-divider::after { content: ''; flex: 1; height: 1px; background: var(--line); }
  .sign-in-email { display: flex; gap: 8px; }
  .sign-in-email input { flex: 1; font-size: 14px; padding: 11px 12px; }
  .sign-in-email .primary { gap: 0; padding: 11px 16px; white-space: nowrap; }
  .sign-in-sent { font-size: 13px; line-height: 1.5; margin-bottom: 16px; overflow-wrap: anywhere; }
  .otp { position: relative; display: block; }
  .otp input { position: absolute; inset: 0; opacity: 0; width: 100%; font-size: 16px; caret-color: transparent; }
  .otp-cells { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
  .otp-cells span { height: 54px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
    font: 500 24px/1 ui-monospace, SFMono-Regular, Consolas, monospace; transition: border-color .15s, transform .15s; }
  .otp-cells span.filled { border-color: var(--line-strong); animation: pop .18s ease; }
  .otp:focus-within .otp-cells span.caret { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-wash); }
  .otp:focus-within .otp-cells span.caret::after { content: ''; width: 1.5px; height: 24px; background: var(--accent); animation: blink 1s steps(1) infinite; }
  .otp.busy .otp-cells span { border-color: var(--accent); animation: wave 1s ease-in-out infinite; }
  .otp.busy .otp-cells span:nth-child(2) { animation-delay: .08s } .otp.busy .otp-cells span:nth-child(3) { animation-delay: .16s }
  .otp.busy .otp-cells span:nth-child(4) { animation-delay: .24s } .otp.busy .otp-cells span:nth-child(5) { animation-delay: .32s }
  .otp.busy .otp-cells span:nth-child(6) { animation-delay: .4s }
  @keyframes pop { 50% { transform: scale(1.06) } }
  @keyframes blink { 50% { opacity: 0 } }
  @keyframes wave { 50% { transform: translateY(-3px) } }
  .sign-in-code-actions { display: flex; justify-content: space-between; margin-top: 14px; }
  .sign-in-terms { font-size: 11px; line-height: 1.55; color: var(--muted); margin-top: 22px; }
  .sign-in-terms a { text-decoration: underline; text-underline-offset: 2px; }
  .sign-in-skip { align-self: center; margin-top: 16px; }
  .sign-in-wide { width: 100%; justify-content: center; gap: 10px; margin-top: 4px; }
</style>
