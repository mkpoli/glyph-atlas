// Drawing sequences in a worker (`worker.js`) as the editor asks for them: one at a time, the newest
// only. Asking for another sequence while one is being drawn ends that worker and starts a new one,
// since a drawing in progress cannot be interrupted; the files it had loaded stay in the browser's
// cache, the export's files being immutable.

/**
 * A drawer over workers made by `makeWorker`, drawing from the export at `base`. `draw(sequence,
 * onSketch)` answers `{ status: 'composed', drawn }`, `{ status: 'failed', error }`, or `{ status:
 * 'cancelled' }` when it was replaced or `cancel` was called first; `onSketch` hears where the layout
 * puts each part before the drawing is done.
 */
export function createDrawer(makeWorker, base) {
  let worker = null, pending = null, next = 0
  const settle = answer => { const p = pending; pending = null; p?.resolve(answer) }
  function end() {
    worker?.terminate()
    worker = null
  }
  function cancel() {
    if (!pending) return
    end()
    settle({ status: 'cancelled' })
  }
  function draw(sequence, onSketch = null) {
    cancel()
    if (!worker) {
      worker = makeWorker()
      worker.onmessage = ({ data }) => {
        if (data.id !== pending?.id) return
        if (data.sketch) { pending.onSketch?.(data.sketch); return }
        settle(data.error === undefined ? { status: 'composed', drawn: data.drawn } : { status: 'failed', error: data.error })
      }
      worker.onerror = event => { event?.preventDefault?.(); end(); settle({ status: 'failed', error: String(event?.message ?? 'worker failed') }) }
    }
    const id = ++next
    return new Promise(resolve => {
      pending = { id, resolve, onSketch }
      worker.postMessage({ id, sequence, base })
    })
  }
  return { draw, cancel, dispose: () => { cancel(); end() } }
}

/**
 * A painter over one worker, for drawings shown in place rather than edited: `paint(sequence)` draws
 * every sequence asked of it, none ending another, and answers the composer's drawing (`compose` in
 * `index.js`) or null where it cannot draw it. A worker that fails answers null for all it held, and
 * the next request starts another.
 */
export function createPainter(makeWorker, base) {
  let worker = null, next = 0
  const pending = new Map()
  function start() {
    worker = makeWorker()
    worker.onmessage = ({ data }) => {
      if (data.sketch || !pending.has(data.id)) return
      const resolve = pending.get(data.id)
      pending.delete(data.id)
      resolve(data.error === undefined ? data.drawn : null)
    }
    worker.onerror = event => {
      event?.preventDefault?.()
      worker?.terminate()
      worker = null
      for (const resolve of pending.values()) resolve(null)
      pending.clear()
    }
  }
  return {
    paint(sequence) {
      if (!worker) start()
      const id = ++next
      return new Promise(resolve => { pending.set(id, resolve); worker.postMessage({ id, sequence, base }) })
    },
  }
}
