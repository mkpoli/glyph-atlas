import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

// The outbox outside a browser: kept saves live in this localStorage, which a test may also write as
// another tab would. IndexedDB is a map that refuses every transaction unless a test opens it, or
// refuses only writes. `$state` is a plain object here.
const store = new Map()
const idb = { saves: new Map(), open: false, refusesPut: false }
const copy = value => value === undefined ? undefined : structuredClone(value)
const idbDatabase = {
  transaction() {
    if (!idb.open) throw new Error('IndexedDB refused')
    const run = { objectStore: () => ({
      getAll: () => ({ result: [...idb.saves.values()].map(copy) }),
      get: key => ({ result: copy(idb.saves.get(key)) }),
      put: entry => { if (idb.refusesPut) throw new Error('IndexedDB full'); idb.saves.set(entry.key, copy(entry)); return { result: entry.key } },
      delete: key => { idb.saves.delete(key); return { result: undefined } },
    }) }
    queueMicrotask(() => run.oncomplete?.())
    return run
  },
}
globalThis.indexedDB = { open() { const asked = { result: idbDatabase }; queueMicrotask(() => asked.onsuccess()); return asked } }
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
// A copy of its own: another test file may have loaded the module, with its IndexedDB, before.
const { deliver, dismiss, drain, outbox, useSender } = await import('../src/lib/outbox.svelte.js?idb')
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
  idb.saves.clear(); idb.open = false; idb.refusesPut = false
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

describe('a save this page waits on that another tab found gone', () => {
  it('is refused to the view and removed, without being sent', async () => {
    const path = '/atlas/characters/hk%3A5%3Aa', key = path + '#waited'
    const answered = deliver(path, { id: 'waited', character: '星' }, null, 5).then(() => null, error => error)
    while (!kept().some(e => e.key === key)) await Bun.sleep(1)
    keep(kept().map(e => e.key === key ? { ...e, gone: true } : e))
    await drain()
    expect((await answered)?.message).toBe('gone')
    expect(send).not.toHaveBeenCalled()
    expect(kept()).toEqual([])
    expect(outbox.refused).toEqual([])
  })
})

describe('a gone save discarded while a pass is running', () => {
  it('is not sent by that pass, nor by a later one', async () => {
    const first = entry('hk:3:a', '火'), later = entry('hk:3:b', '水')
    keep([first, later])
    let release
    send.mockImplementationOnce(() => new Promise(done => { release = () => done(accepted()) }))
    const running = drain()
    while (!release) await Bun.sleep(1)
    // Another tab found the later save gone; this page shows it once it counts again, and the
    // reader discards it while the first send is still out.
    keep(kept().map(e => e.key === later.key ? { ...e, gone: true } : e))
    const fresh = deliver('/atlas/characters/hk%3A3%3Ac', { id: 'fresh', character: '木' }, null)
    while (!outbox.refused.some(r => r.key === later.key)) await Bun.sleep(1)
    await dismiss(later.key)
    release()
    await running
    await fresh
    await drain()
    const sent = send.mock.calls.map(([e]) => e.key)
    expect(sent).not.toContain(later.key)
    expect(sent).toEqual([first.key, '/atlas/characters/hk%3A3%3Ac#fresh'])
    expect(kept()).toEqual([])
  })

  it('read by the pass before it was marked gone is still skipped once discarded', async () => {
    const first = entry('hk:4:a', '金'), later = entry('hk:4:b', '土')
    keep([first, later])
    let release
    send.mockImplementationOnce(() => new Promise(done => { release = () => done(accepted()) }))
    const running = drain()
    while (!release) await Bun.sleep(1)
    // The corner shows it as gone (as after another tab's pass) and the reader discards it.
    outbox.refused.push({ key: later.key, gone: true, what: '土' })
    await dismiss(later.key)
    release()
    await running
    expect(send.mock.calls.map(([e]) => e.key)).toEqual([first.key])
    expect(kept()).toEqual([])
  })
})

describe('a save with a copy in IndexedDB and one in localStorage', () => {
  it('is read as the localStorage copy by both the count and the pass', async () => {
    idb.open = true
    const held = entry('hk:6:a', '雨')
    idb.saves.set(held.key, held)
    // IndexedDB refused the write that marked it gone, so the gone copy went to localStorage.
    keep([{ ...held, gone: true }])
    await drain()
    expect(send).not.toHaveBeenCalled()
    expect(outbox.pending).toBe(0)
    expect(outbox.refused).toEqual([{ key: held.key, gone: true, what: '雨 · hk:6:a' }])
  })

  it('keeps one copy once marked gone in IndexedDB', async () => {
    idb.open = true
    const held = entry('hk:7:a', '雲')
    keep([held])
    send.mockImplementation(async () => gone())
    await drain()
    expect(kept()).toEqual([])
    expect(idb.saves.get(held.key)).toEqual({ ...held, gone: true })
    await drain()
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('keeps one copy once marked gone in localStorage', async () => {
    idb.open = true; idb.refusesPut = true
    const held = entry('hk:8:a', '雪')
    idb.saves.set(held.key, held)
    send.mockImplementation(async () => gone())
    await drain()
    expect(kept()).toEqual([{ ...held, gone: true }])
    expect(idb.saves.has(held.key)).toBe(false)
    await drain()
    expect(send).toHaveBeenCalledTimes(1)
  })
})
