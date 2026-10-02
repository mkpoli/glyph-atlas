// The accounts client, loaded once the page runs in a browser; no page needs it to draw.
let loaded
export function authClient() {
  loaded ??= Promise.all([import('better-auth/client'), import('better-auth/client/plugins')])
    .then(([{ createAuthClient }, { anonymousClient }]) => createAuthClient({
      basePath: '/api/auth',
      plugins: [anonymousClient()],
    }))
  return loaded
}
