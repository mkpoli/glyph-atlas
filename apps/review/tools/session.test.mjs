// What a page asks about its reader once it runs in a browser.
import { afterAll, expect, mock, test } from 'bun:test'

globalThis.$state = value => value
const asked = []
let answer = () => ({ user: null })
mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key }))
mock.module('../src/lib/auth.js', () => ({ authClient: async () => { throw new Error('no accounts client in this test') } }))
mock.module('../src/lib/client.js', () => ({
  stored: (_key, fallback) => fallback, remember() {}, forget() {},
  request: async path => { asked.push(path); return answer() },
}))
const { createSession } = await import('../src/lib/session.svelte.js')
afterAll(() => mock.restore())

const reader = { id: 'u1', name: 'Ada', image: null, anonymous: false, admin: false }
async function started(account) {
  asked.length = 0
  const session = createSession(account)
  await session.start()
  return session.state
}

test('a page whose server read the session asks nothing, signed in or not', async () => {
  expect(await started({ user: null, known: true, providers: [] })).toMatchObject({ user: null, ready: true })
  expect(asked).toEqual([])
  expect(await started({ user: reader, known: true, providers: [] })).toMatchObject({ user: reader, ready: true })
  expect(asked).toEqual([])
})

test('a page whose server could not read the session asks who reads', async () => {
  answer = () => ({ user: reader })
  expect(await started({ user: null, known: false, providers: [] })).toMatchObject({ user: reader, ready: true })
  expect(asked).toEqual(['/api/account'])
  // A page drawn without the layout's answer asks too.
  answer = () => { throw new Error('signed out') }
  expect(await started(undefined)).toMatchObject({ user: null, ready: true })
  expect(asked).toEqual(['/api/account'])
})
