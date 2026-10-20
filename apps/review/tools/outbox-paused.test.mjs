// The outbox while the site pauses writes (the Worker answers 503 `paused` with a Retry-After). The
// rune modules run under bun with `$state` as a plain value and the strings as their keys.
import { afterEach, beforeEach, expect, mock, test } from 'bun:test'

globalThis.$state = value => value
globalThis.window = globalThis
// One tab's localStorage, which every tab of the site shares; a test plays another tab by writing it.
const shelf = new Map()
globalThis.localStorage = { getItem: key => shelf.get(key) ?? null, setItem: (key, value) => shelf.set(key, String(value)), removeItem: key => shelf.delete(key) }
globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key, formatNumber: String }))
// Nobody is signed in, and starting an anonymous session is refused as a write would be.
const signIns = []
let me = null
mock.module('../src/lib/session.svelte.js', () => ({
  sessionStarted: async () => {},
  signedInUser: () => me,
  ensureSignedIn: async () => {
    if (me) return me
    signIns.push(Date.now())
    throw Object.assign(new Error('client.paused'), { code: 'paused', status: 503, retryAfter: 300 })
  },
}))

const { backoff } = await import('../src/lib/busy.svelte.js')
const { request } = await import('../src/lib/client.js')
const { outbox, drain, keep, keptCrops, keptState, keyOf, dismiss, resume } = await import('../src/lib/outbox.svelte.js')
const { correctionBatches, restoredCrops, sendBatches, sendEach } = await import('../src/lib/corrections.js')

const paused = () => Response.json({ error: 'paused', message: 'Changes are paused.' }, { status: 503, headers: { 'retry-after': '300' } })
let posts, sent, timers, realFetch, realTimeout, answer
beforeEach(() => {
  posts = []; sent = []; timers = []; signIns.length = 0; answer = paused
  realFetch = globalThis.fetch; realTimeout = globalThis.setTimeout
  globalThis.fetch = async (path, init) => { posts.push(path); sent.push(init?.body ? JSON.parse(init.body).id : null); return answer() }
  // Timers are recorded, never run: what matters is how long the outbox waits.
  globalThis.setTimeout = (run, ms) => { timers.push(ms); return timers.length }
})
afterEach(() => { globalThis.fetch = realFetch; globalThis.setTimeout = realTimeout })

test('backoff waits out a Retry-After past the 30 s cap, and never less', () => {
  for (let n = 0; n < 20; n++) {
    const wait = backoff(n, 300)
    expect(wait).toBeGreaterThanOrEqual(300_000)
    expect(wait).toBeLessThanOrEqual(375_000)
    expect(backoff(n)).toBeLessThanOrEqual(37_500)
  }
})

test('a save refused while paused is kept and its caller hears so at once; the sign-in is asked again only after the Retry-After', async () => {
  const saving = request('/atlas/rounds', { id: 'round-1', grapheme: 'U+3042', seen: [{ id: 'c1' }], skipped: [] })
  const error = await saving.catch(e => e)
  expect(error.kept).toBe(true)
  expect(error.code).toBe('paused')
  expect(error.message).toBe('client.pausedKept')
  // The session could not start, so nothing was posted, and the save is kept.
  expect(posts).toEqual([])
  expect(signIns.length).toBe(1)
  expect(outbox.pending).toBe(1)
  expect(outbox.paused).toBe(true)
  expect(timers.at(-1)).toBeGreaterThanOrEqual(300_000)
  // Sending again asks for a session once, and is told to wait the Retry-After again.
  await drain()
  expect(signIns.length).toBe(2)
  expect(posts).toEqual([])
  expect(timers.at(-1)).toBeGreaterThanOrEqual(300_000)
  // A second save joins the line and is not left waiting on the pause.
  const second = await request('/atlas/rounds', { id: 'round-2', grapheme: 'U+3044', seen: [{ id: 'c2' }], skipped: [] }).catch(e => e)
  expect(second.kept).toBe(true)
  expect(outbox.pending).toBe(2)
})

test('a signed-in save refused while paused is kept, and one queued behind it is released when the line is refused', async () => {
  me = { id: 'u1', anonymous: false }
  // The site takes writes again: the two saves kept above go, and the pause is over.
  answer = () => Response.json({ ok: true })
  await drain()
  expect(outbox.pending).toBe(0)
  expect(outbox.paused).toBe(false)
  answer = paused; posts.length = 0
  const first = await request('/atlas/rounds', { id: 'round-3', grapheme: 'U+3046', seen: [{ id: 'c3' }], skipped: [] }).catch(e => e)
  expect(first.kept).toBe(true)
  expect(posts).toEqual(['/atlas/rounds'])
  expect(outbox.pending).toBe(1)
  expect(timers.at(-1)).toBeGreaterThanOrEqual(300_000)
  // The next save joins the line; sending the line meets the pause and lets its caller go.
  const second = await request('/atlas/rounds', { id: 'round-4', grapheme: 'U+3048', seen: [{ id: 'c4' }], skipped: [] }).catch(e => e)
  expect(second.kept).toBe(true)
  expect(posts).toEqual(['/atlas/rounds', '/atlas/rounds'])
  expect(outbox.pending).toBe(2)
  expect(signIns.length).toBe(0)
})

test('a round dealt while saves are kept leaves out their crops, unless their undo is kept too', async () => {
  // round-3 and round-4 from above are still kept.
  expect([...await keptCrops()].sort()).toEqual(['c3', 'c4'])
  await keep('/atlas/corrections', { id: 'batch-1', character: 'あ', crops: [{ id: 'c5', revision: 1 }] }, me)
  await keep('/atlas/rounds/round-3/undo', {}, me)
  expect([...await keptCrops()].sort()).toEqual(['c4', 'c5'])
  // A round another account kept on this device waits for it, and its crops are this reader's to deal.
  await keep('/atlas/rounds', { id: 'round-other', grapheme: 'U+3042', seen: [{ id: 'c9' }], skipped: [] }, { id: 'u2', anonymous: false })
  expect([...await keptCrops()].sort()).toEqual(['c4', 'c5'])
})

test('a save kept while a pass is sending is sent by that pass', async () => {
  answer = () => Response.json({ ok: true })
  // The first send waits until the save below is kept.
  let open
  const gate = new Promise(resolve => { open = resolve })
  const plain = globalThis.fetch
  globalThis.fetch = async (path, init) => { const reply = await plain(path, init); if (posts.length === 1) await gate; return reply }
  const pass = drain()
  await new Promise(resolve => realTimeout(resolve, 10))
  expect(posts.length).toBe(1)
  await keep('/atlas/rounds', { id: 'round-5', grapheme: 'U+304A', seen: [{ id: 'c6' }], skipped: [] }, me)
  open()
  await pass
  expect(sent).toContain('round-5')
  expect(outbox.pending).toBe(0)
})

test('a batch correction keeps every batch after a kept one, under the same ids each time', async () => {
  const crops = Array.from({ length: 5 }, (_, i) => ({ id: 'k' + i, revision: 1 }))
  const batches = correctionBatches('い', crops, 2)
  expect(batches.map(batch => batch.crops.length)).toEqual([2, 2, 1])
  expect(correctionBatches('い', crops, 2).map(batch => batch.id)).toEqual(batches.map(batch => batch.id))
  // Another revision or character is another submission.
  expect(correctionBatches('い', [{ id: 'k0', revision: 2 }, crops[1]], 2)[0].id).not.toBe(batches[0].id)
  expect(correctionBatches('う', crops, 2)[0].id).not.toBe(batches[0].id)
  answer = paused; posts.length = 0; sent.length = 0
  const send = ({ id, crops: part }) => request('/atlas/corrections', { id, character: 'い', crops: part })
  const { answered, kept } = await sendEach(batches, send)
  expect(answered).toEqual([])
  expect(kept.map(batch => batch.id)).toEqual(batches.map(batch => batch.id))
  expect(outbox.pending).toBe(3)
  expect([...await keptCrops()].sort()).toEqual(['k0', 'k1', 'k2', 'k3', 'k4'])
  // Pressed again, the same batches are kept under the same keys: nothing is added.
  await sendEach(correctionBatches('い', crops, 2), send)
  expect(outbox.pending).toBe(3)
  // Writes resume: each batch is sent once, after the pass that met the pause has ended.
  await new Promise(resolve => realTimeout(resolve, 10))
  answer = () => Response.json({ results: [] }); sent.length = 0
  await drain()
  expect(sent).toEqual(batches.map(batch => batch.id))
  expect(outbox.pending).toBe(0)
})

test('a batch corrected, undone and corrected again is a new submission at the undo\'s revisions, which the site records', async () => {
  // The site answers a repeated submission with its first answer and changes nothing, and refuses a
  // crop sent at a revision it has moved past. Each write moves a crop one revision on.
  const answers = new Map(), state = {}, revision = { u0: 4, u1: 4 }
  globalThis.fetch = async (path, init) => {
    const body = JSON.parse(init.body)
    posts.push(path); sent.push(body.id ?? null)
    const undone = /^\/atlas\/corrections\/([^/]+)\/undo$/.exec(path)?.[1]
    if (undone) {
      const results = answers.get(undone).crops.map(crop => { state[crop] = 'before'; return { target_id: crop, revision: ++revision[crop] } })
      return Response.json({ id: undone, results })
    }
    if (!answers.has(body.id)) {
      if (body.crops.some(crop => crop.revision !== revision[crop.id])) return Response.json({ detail: 'This crop changed.' }, { status: 409 })
      const results = body.crops.map(crop => { state[crop.id] = body.character; return { target_id: crop.id, revision: ++revision[crop.id] } })
      answers.set(body.id, { crops: body.crops.map(crop => crop.id), results })
    }
    return Response.json({ results: answers.get(body.id).results })
  }
  const crops = [{ id: 'u0', revision: 4 }, { id: 'u1', revision: 4 }]
  const send = ({ id, crops: part }) => request('/atlas/corrections', { id, character: 'え', crops: part })
  const first = await sendBatches(correctionBatches('え', crops, 2), send)
  expect(first.answered.length).toBe(1)
  const undo = await sendEach(first.answered.map(({ item }) => item.id), id => request(`/atlas/corrections/${id}/undo`, {}))
  expect(state).toEqual({ u0: 'before', u1: 'before' })
  // The view puts the crops back as they were before the correction, at the revisions the undo answered.
  const before = Object.fromEntries(crops.map(crop => [crop.id, crop]))
  const restored = restoredCrops(before, undo.answered)
  expect(restored).toEqual({ u0: { id: 'u0', revision: 6 }, u1: { id: 'u1', revision: 6 } })
  // At the revisions they had before the correction, the crops are refused.
  expect((await sendBatches(correctionBatches('え', crops, 2), send).catch(e => e)).status).toBe(409)
  const again = await sendBatches(correctionBatches('え', Object.values(restored), 2), send)
  expect(again.answered[0].item.id).not.toBe(first.answered[0].item.id)
  expect(state).toEqual({ u0: 'え', u1: 'え' })
  // An undo answer that names no revision for a crop leaves the view to read it again.
  expect(restoredCrops(before, [{ result: { results: [] } }])).toBeNull()
})

test('a batch the site refused with a 4xx is a new submission next time; one met by a 5xx or a failed sending keeps its id', async () => {
  const crops = [{ id: 'r0', revision: 1 }]
  const send = ({ id, crops: part }) => request('/atlas/corrections', { id, character: 'お', crops: part })
  answer = () => Response.json({ detail: 'changed' }, { status: 409 })
  const refused = correctionBatches('お', crops, 1)[0].id
  expect((await sendBatches(correctionBatches('お', crops, 1), send).catch(e => e)).status).toBe(409)
  const next = correctionBatches('お', crops, 1)[0].id
  expect(next).not.toBe(refused)
  // A 5xx may come after the site recorded the batch: sent again, it must be the same submission.
  answer = () => Response.json({ detail: 'internal' }, { status: 500 })
  expect((await sendBatches(correctionBatches('お', crops, 1), send).catch(e => e)).status).toBe(500)
  expect(correctionBatches('お', crops, 1)[0].id).toBe(next)
  globalThis.fetch = async () => { throw new Error('connection reset') }
  expect((await sendBatches(correctionBatches('お', crops, 1), send).catch(e => e)).message).toBe('connection reset')
  expect(correctionBatches('お', crops, 1)[0].id).toBe(next)
})

test('a kept round reads as kept while it waits, as sent once taken, and as refused once refused, even after the note is dismissed', async () => {
  me = { id: 'u1', anonymous: false }
  answer = () => Response.json({ ok: true })
  await new Promise(resolve => realTimeout(resolve, 10))
  await drain()
  answer = paused
  const taken = keyOf('/atlas/rounds', { id: 'round-taken' }), refused = keyOf('/atlas/rounds', { id: 'round-refused' })
  for (const id of ['round-taken', 'round-refused']) await keep('/atlas/rounds', { id, grapheme: 'U+304B', seen: [{ id: id + '-crop' }], skipped: [] }, me)
  expect([keptState(taken), keptState(refused)]).toEqual(['kept', 'kept'])
  // Writes resume; the site takes one round and refuses the other.
  await new Promise(resolve => realTimeout(resolve, 10))
  globalThis.fetch = async (path, init) => {
    const { id } = JSON.parse(init.body)
    return id === 'round-refused' ? Response.json({ detail: 'This crop changed.' }, { status: 409 }) : Response.json({ ok: true })
  }
  await drain()
  expect([keptState(taken), keptState(refused)]).toEqual(['sent', 'refused'])
  expect(outbox.refused.map(r => r.key)).toContain(refused)
  dismiss(refused)
  expect(keptState(refused)).toBe('refused')
})

test('a run of saves stops at a refusal, and says what came before it', async () => {
  const refusal = Object.assign(new Error('refused'), { status: 409 })
  const results = { a: 1, c: 3 }
  const error = await sendEach(['a', 'b', 'c', 'd'], async item => {
    if (item === 'b') throw Object.assign(new Error('kept'), { kept: true })
    if (item === 'd') throw refusal
    return results[item]
  }).catch(e => e)
  expect(error).toBe(refusal)
  expect(error.answered).toEqual([{ item: 'a', result: 1 }, { item: 'c', result: 3 }])
  expect(error.kept).toEqual(['b'])
  expect(error.item).toBe('d')
})

test('a kept round stays kept while its account is signed out, and reads as refused once another tab saw it refused', async () => {
  me = { id: 'u7', anonymous: false }
  answer = paused
  const key = keyOf('/atlas/rounds', { id: 'round-away' })
  await keep('/atlas/rounds', { id: 'round-away', grapheme: 'U+304D', seen: [{ id: 'away-crop' }], skipped: [] }, me)
  expect(keptState(key)).toBe('kept')
  // Signed out (or the session lapsed), the save waits for its account and is still stored here.
  me = null
  await new Promise(resolve => realTimeout(resolve, 10))
  await drain()
  expect(outbox.keys).toContain(key)
  expect(keptState(key)).toBe('kept')
  // Another tab, signed in as u7, sends it and the site refuses it: that tab drops it and names it.
  const stop = resume()
  const stored = JSON.parse(localStorage.getItem('atlas.outbox')).filter(entry => entry.key !== key)
  localStorage.setItem('atlas.outbox', JSON.stringify(stored))
  localStorage.setItem('atlas.outbox.lost', JSON.stringify([key]))
  dispatchEvent(Object.assign(new Event('storage'), { key: 'atlas.outbox.lost' }))
  await new Promise(resolve => realTimeout(resolve, 10))
  expect(keptState(key)).toBe('refused')
  stop()
})
