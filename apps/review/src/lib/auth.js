// The accounts client, loaded once the page runs in a browser; no page needs it to draw.
let loaded
export function authClient() {
  loaded ??= Promise.all([import('better-auth/client'), import('better-auth/client/plugins'), import('@better-auth/passkey/client')])
    .then(([{ createAuthClient }, { anonymousClient, emailOTPClient, lastLoginMethodClient }, { passkeyClient }]) => createAuthClient({
      basePath: '/api/auth',
      plugins: [anonymousClient(), emailOTPClient(), passkeyClient(), lastLoginMethodClient()],
    }))
  return loaded
}

/** The ways to sign in this browser can offer, in the order the form shows them. */
export const METHODS = ['passkey', 'github', 'google', 'discord', 'line', 'kakao', 'email-otp']

/** Whether this browser can use a passkey, and offer one from the address field. */
export async function passkeys() {
  if (typeof PublicKeyCredential === 'undefined') return { supported: false, autofill: false }
  const autofill = await PublicKeyCredential.isConditionalMediationAvailable?.().catch(() => false)
  return { supported: true, autofill: Boolean(autofill) }
}
