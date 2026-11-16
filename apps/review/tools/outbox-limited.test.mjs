// The outbox while the site refuses an address that asks too often (429 with a Retry-After). The rune
// modules run under bun with `$state` as a plain value and the strings as their keys.
import { afterEach, beforeEach, expect, mock, test } from 'bun:test'

globalThis.$state = value => value
globalThis.window = globalThis
const shelf = new Map()
globalThis.localStorage = { getItem: key => shelf.get(key) ?? null, setItem: (key, value) => shelf.set(key, String(value)), removeItem: key => shelf.delete(key) }
globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key, formatNumber: String }))
// A signed-in reader, or nobody: starting an anonymous session is then refused as any request is.
const signIns = []
let me = null
mock.module('../src/lib/session.svelte.js', () => ({
  sessionStarted: async () => {},
  signedInUser: () => me,
  readerKnown: () => true,
  ensureSignedIn: async () => {
    if (me) return me
    signIns.push(Date.now())
    throw Object.assign(new Error('Too many requests'), { code: 'rate_limited', status: 429, retryAfter: 0 })
  },
}))

const { request } = await import('../src/lib/client.js')
const { outbox, drain } = await import('../src/lib/outbox.svelte.js')

const refused = () => Response.json({ detail: 'Too many requests from this address. Try again later.', code: 'rate_limited' }, { status: 429, headers: { 'retry-after': '60' } })
const round = id => ({ id, grapheme: 'U+3042', seen: [{ id: 'c-' + id }], skipped: [] })
let posts, timers, realFetch, realTimeout, answer
beforeEach(() => {
  posts = []; timers = []; signIns.length = 0; answer = refused
  realFetch = globalThis.fetch; realTimeout = globalThis.setTimeout
  globalThis.fetch = async (path, init) => { posts.push(JSON.parse(init.body).id); return answer() }
  // Timers are recorded, never run: what matters is how long the outbox waits.
  globalThis.setTimeout = (run, ms) => { timers.push(ms); return timers.length }
})
afterEach(() => { globalThis.fetch = realFetch; globalThis.setTimeout = realTimeout })

test('a save refused 429 is kept, sent again after the Retry-After, and answers its caller once the site takes it', async () => {
  me = { id: 'u1', anonymous: false }
  let settled = null
  const saving = request('/atlas/rounds', round('round-1')).then(value => { settled = value; return value })
  await Bun.sleep(0)
  // The caller is still waiting, the save is kept, and the next try comes no sooner than the site asked.
  expect(settled).toBeNull()
  expect(posts).toEqual(['round-1'])
  expect(outbox.pending).toBe(1)
  expect(timers.at(-1)).toBeGreaterThanOrEqual(60_000)
  // Still refused: it stays kept and waits the Retry-After again.
  await drain()
  expect(posts).toEqual(['round-1', 'round-1'])
  expect(outbox.pending).toBe(1)
  expect(timers.at(-1)).toBeGreaterThanOrEqual(60_000)
  // A save made meanwhile joins the line behind it and is not sent ahead.
  const second = request('/atlas/rounds', round('round-2'))
  await drain()
  expect(posts).toEqual(['round-1', 'round-1', 'round-1'])
  expect(outbox.pending).toBe(2)
  // The lockout ends: both go, in order, and each caller gets the site's answer.
  answer = () => Response.json({ ok: true })
  await drain()
  expect(posts.slice(3)).toEqual(['round-1', 'round-2'])
  expect(await saving).toEqual({ ok: true })
  expect(await second).toEqual({ ok: true })
  expect(outbox.pending).toBe(0)
  expect(outbox.refused).toEqual([])
})

test('a first save whose session the site refuses to start is kept too', async () => {
  me = null
  let settled = false
  const saving = request('/atlas/rounds', round('round-3')).finally(() => { settled = true })
  await Bun.sleep(0)
  expect(settled).toBe(false)
  expect(posts).toEqual([])
  expect(outbox.pending).toBe(1)
  // Once a session starts, the kept save goes.
  me = { id: 'anon-1', anonymous: true }
  answer = () => Response.json({ ok: true })
  await drain()
  expect(posts).toEqual(['round-3'])
  expect(await saving).toEqual({ ok: true })
  expect(outbox.pending).toBe(0)
})

test('a save that names no submission is not kept, and its caller hears the refusal', async () => {
  me = { id: 'u1', anonymous: false }
  const error = await request('/api/favourites', { crop: 'c1', starred: true }).catch(e => e)
  expect([error.status, error.code]).toEqual([429, 'rate_limited'])
  expect(outbox.pending).toBe(0)
})
