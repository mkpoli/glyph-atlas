// A read on the page server, whose fetch hands back a redirect instead of following it.
import { expect, mock, test } from 'bun:test'

globalThis.$state = value => value
mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key, formatNumber: String }))
mock.module('../src/lib/session.svelte.js', () => ({ sessionStarted: async () => {}, signedInUser: () => null, readerKnown: () => true,
  ensureSignedIn: async () => null }))
const { request } = await import('../src/lib/client.js')

const answers = paths => {
  const asked = []
  const fetch = async path => { asked.push(path); return paths[path]() }
  return { fetch, asked }
}

test('an old crop address is followed once, to the address the Worker names', async () => {
  const { fetch, asked } = answers({
    '/atlas/characters/hk%3A1%3Aa': () => new Response(null, { status: 302, headers: { location: 'https://glyphatlas.org/atlas/characters/3dh00000?x=1' } }),
    '/atlas/characters/3dh00000?x=1': () => Response.json({ id: '3dh00000' }),
  })
  expect(await request('/atlas/characters/hk%3A1%3Aa', undefined, { fetch })).toEqual({ id: '3dh00000' })
  expect(asked).toEqual(['/atlas/characters/hk%3A1%3Aa', '/atlas/characters/3dh00000?x=1'])
})

test('an answer after the redirect that is not the API\'s is an error with its status', async () => {
  const { fetch } = answers({
    '/atlas/characters/old': () => new Response(null, { status: 302, headers: { location: '/atlas/characters/new' } }),
    '/atlas/characters/new': () => new Response('<html>gone</html>', { status: 404, headers: { 'content-type': 'text/html' } }),
  })
  await expect(request('/atlas/characters/old', undefined, { fetch })).rejects.toMatchObject({ status: 404 })
})
