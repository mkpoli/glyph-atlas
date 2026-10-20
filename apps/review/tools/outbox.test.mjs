import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

// The outbox outside a browser: no IndexedDB, so kept saves live in this localStorage, which a test
// may also write as another tab would. `$state` is a plain object here.
const store = new Map()
globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)) }
globalThis.$state = value => value
const FALLBACK = 'atlas.outbox'
const kept = () => JSON.parse(store.get(FALLBACK) ?? '[]')
const keep = entries => store.set(FALLBACK, JSON.stringify(entries))
mock.module('../src/lib/busy.svelte.js', () => ({
  backoff: () => 60000, retryAfter: () => 0,
  isBusy: (response, value) => response.status === 503 && value?.code === 'busy',
  isPaused: () => false,
  isReload: (response, value) => response.status === 409 && value?.code === 'reload',
}))
const { deliver, dismiss, drain, outbox, useSender } = await import('../src/lib/outbox.svelte.js')
afterAll(() => mock.restore())

const answer = (status, value) => ({ response: new Response(null, { status }), value })
const accepted = () => answer(200, { ok: true })
const gone = () => answer(409, { detail: 'This page is from before an update. Reload it.', code: 'reload' })
let n = 0
const entry = (crop, character) => {
  const id = `s${++n}`
  return { key: `/atlas/characters/${encodeURIComponent(crop)}#${id}`, path: `/atlas/characters/${encodeURIComponent(crop)}`,
    body: { id, character }, user: null, anonymous: false, seq: n }
}
let send
beforeEach(() => {
  store.clear(); outbox.pending = 0; outbox.refused = []
  send = mock(async () => accepted())
  useSender({ send: e => send(e), mine: () => true, settle({ response, value }) {
    if (response.ok) return value
    const error = new Error(value.code === 'reload' ? 'gone' : 'refused'); error.code = value.code; throw error
  } })
})

describe('a kept save naming a crop the site no longer holds', () => {
  it('is kept as gone, shown with what it named, and never sent again', async () => {
    const held = entry('hk:1:a', '日')
    keep([held])
    send.mockImplementation(async () => gone())
    await drain()
    expect(send).toHaveBeenCalledTimes(1)
    expect(kept()).toEqual([{ ...held, gone: true }])
    expect(outbox.pending).toBe(0)
    expect(outbox.refused).toEqual([{ key: held.key, gone: true, what: '日 · hk:1:a' }])
    // A later pass, or the next page, does not send it again.
    await drain()
    expect(send).toHaveBeenCalledTimes(1)
    await dismiss(held.key)
    expect(kept()).toEqual([])
    expect(outbox.refused).toEqual([])
  })

  it('made on this page is refused to the view, and not kept', async () => {
    send.mockImplementation(async () => gone())
    await expect(deliver('/atlas/characters/hk%3A2%3Aa', { id: 'own', character: '月' }, null)).rejects.toThrow('gone')
    expect(kept()).toEqual([])
    expect(outbox.refused).toEqual([])
  })
})
