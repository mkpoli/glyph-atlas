// Drawing sequences in a worker (`worker.js`) as the editor asks for them: one at a time, the newest
// only. Asking for another sequence while one is being drawn ends that worker and starts a new one,
// since a drawing in progress cannot be interrupted; the files it had loaded stay in the browser's
// cache, the export's files being immutable.

/**
 * A drawer over workers made by `makeWorker`, drawing from the export at `base`. `draw(sequence)`
 * answers `{ status: 'composed', drawn }`, `{ status: 'failed', error }`, or `{ status: 'cancelled' }`
 * when it was replaced or `cancel` was called first.
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
  function draw(sequence) {
    cancel()
    if (!worker) {
      worker = makeWorker()
      worker.onmessage = ({ data }) => {
        if (data.id !== pending?.id) return
        settle(data.error === undefined ? { status: 'composed', drawn: data.drawn } : { status: 'failed', error: data.error })
      }
      worker.onerror = event => { event?.preventDefault?.(); end(); settle({ status: 'failed', error: String(event?.message ?? 'worker failed') }) }
    }
    const id = ++next
    return new Promise(resolve => {
      pending = { id, resolve }
      worker.postMessage({ id, sequence, base })
    })
  }
  return { draw, cancel, dispose: () => { cancel(); end() } }
}
