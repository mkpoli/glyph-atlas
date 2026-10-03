import { getContext, setContext } from 'svelte'
import { authClient } from './auth.js'
import { stored, remember, forget, request } from './client.js'
import { t } from './i18n.svelte.js'

const KEY = Symbol('session')
// The id this browser reviewed under before accounts, until an account takes it over.
const LEGACY = 'atlas.reviewer'

let current = null

/**
 * What the layout knows about this browser and shares with every view: who is signed in, which is
 * known once the page runs in a browser, the image style, whether a save in the inspector goes on to
 * the next crop, and the collection-progress dialog.
 */
export function createSession(account = { user: null, providers: [] }) {
  const state = $state({ ready: false, user: account.user, providers: account.providers, signingIn: false, ink: 'original', advance: false, progress: false })
  let starting = null
  const session = {
    state,
    async start() {
      // A manuscript scan is a colour photograph of paper and ink, so Original is the default and B&W
      // is the reader's choice. One preference serves the collection, the round and the reviewer.
      state.ink = stored('atlas.ink', 'original') === 'bw' ? 'bw' : 'original'
      // Most crops in the collection are right, so a save closes the inspector unless the reader has
      // chosen to go through them in a row.
      state.advance = stored('atlas.advance', false) === true
      // The page arrives knowing who is signed in. If it could not tell, the browser asks, waiting out a
      // busy database, so a reader who is signed in is never taken for a new one.
      if (!state.user) try { state.user = (await request('/api/account')).user ?? null } catch { /* Signed out. */ }
      // A browser that reviewed before accounts brings that work into its session.
      try { if (stored(LEGACY, null)) await session.ensure() } catch { /* The first write tries again. */ }
      state.ready = true
    },
    /** The signed-in user, starting an anonymous session if there is none. */
    async ensure({ again = false } = {}) {
      // An account whose session has ended (signed out elsewhere, or banned) signs in again; it never
      // goes on saving as somebody new.
      if (again && state.user && !state.user.anonymous) { state.user = null; state.signingIn = true; throw new Error(t('client.signInAgain')) }
      if (again) state.user = null
      if (state.user) { await claimLegacy(); return state.user }
      starting ??= (async () => {
        const { data, error } = await (await authClient()).signIn.anonymous()
        if (error) throw new Error(error.message)
        state.user = user(data.user)
        await claimLegacy()
        return state.user
      })().finally(() => { starting = null })
      return starting
    },
    /** Read who is signed in afresh, past the session's cached copy. */
    async refresh() {
      const { data } = await (await authClient()).getSession({ query: { disableCookieCache: true } })
      state.user = data?.user ? user(data.user) : null
      return state.user
    },
    async signOut() {
      await (await authClient()).signOut()
      state.user = null
    },
    /** Open the sign-in form over the page. */
    signIn() { state.signingIn = true },
    setInk(value) { state.ink = value; remember('atlas.ink', value) },
    setAdvance(value) { state.advance = value; remember('atlas.advance', value) },
    showProgress() { state.progress = true },
  }
  current = session
  return session
}

const user = value => ({ id: value.id, name: value.name, image: value.image ?? null, anonymous: Boolean(value.isAnonymous), admin: value.role === 'admin' })

async function claimLegacy() {
  const reviewer = stored(LEGACY, null)
  if (!reviewer) return
  const response = await fetch('/api/account/claim', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ reviewer }) })
  // Held now, or held by another account, the id is settled from here. A failure tries again next time.
  if ([200, 409, 422, 429].includes(response.status)) forget(LEGACY)
}

/** Make sure the browser is signed in before a write. */
export const ensureSignedIn = options => current?.ensure(options)

export const provideSession = session => setContext(KEY, session)
export const useSession = () => getContext(KEY)
