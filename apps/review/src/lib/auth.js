// The accounts client, loaded once the page runs in a browser; no page needs it to draw.
let loaded
export function authClient() {
  loaded ??= Promise.all([import('better-auth/client'), import('better-auth/client/plugins'), import('@better-auth/passkey/client')])
    .then(([{ createAuthClient }, { adminClient, anonymousClient, emailOTPClient, lastLoginMethodClient }, { passkeyClient }]) => createAuthClient({
      basePath: '/api/auth',
      plugins: [anonymousClient(), emailOTPClient(), passkeyClient(), lastLoginMethodClient(), adminClient()],
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

/** A browser and its system, read from a user agent: "Chrome · Windows", or '' when neither is known. */
export function deviceName(agent = typeof navigator === 'undefined' ? '' : navigator.userAgent) {
  const browser = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox' : /Chrome\//.test(agent) ? 'Chrome' : /Safari\//.test(agent) ? 'Safari' : ''
  const system = /iPhone|iPad/.test(agent) ? 'iOS' : /Android/.test(agent) ? 'Android' : /Mac OS X/.test(agent) ? 'macOS' : /Windows/.test(agent) ? 'Windows' : /Linux/.test(agent) ? 'Linux' : ''
  return [browser, system].filter(Boolean).join(' · ')
}

/**
 * Add a passkey for the signed-in account. The authenticator files it under the account's email
 * address (or its name, without one), which a password manager shows as the username; the site
 * labels it with the device it was made on.
 */
export async function addPasskey(client) {
  const { data } = await client.getSession()
  const email = data?.user?.email
  const account = email && !email.endsWith('.invalid') ? email : data?.user?.name
  const added = await client.passkey.addPasskey({ name: account || undefined })
  const label = deviceName()
  if (!added.error && added.data?.id && label) await client.passkey.updatePasskey({ id: added.data.id, name: label })
  return added
}
