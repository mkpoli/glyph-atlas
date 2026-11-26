// Drawing sequences in a Web Worker (`worker.js`). The editor preview uses han-compose's
// `createDrawer`; in-place glyph painting (`paint.js`) queues sequences on one shared worker.
export { createDrawer } from 'han-compose'

/**
 * A painter over one worker, for drawings shown in place rather than edited: `paint(sequence)` draws
 * every sequence asked of it, none ending another, and answers the composer's drawing or null where
 * it cannot draw it. A worker that fails answers null for all it held, and the next request starts
 * another.
 */
export function createPainter(makeWorker, base, config = {}) {
  if (!base.endsWith('/')) base = `${base}/`
  const loaders = config.loaders ?? {}
  let worker = null, next = 0
  const pending = new Map()
  function start() {
    worker = makeWorker()
    worker.onmessage = ({ data }) => {
      if (data.sketch || data.warm || !pending.has(data.id)) return
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
      return new Promise(resolve => { pending.set(id, resolve); worker.postMessage({ id, sequence, base, loaders }) })
    },
  }
}
