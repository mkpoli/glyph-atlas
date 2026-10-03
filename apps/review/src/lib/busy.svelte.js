// While the site's database takes an import, the Worker answers 503 with `code: 'busy'` and a
// `Retry-After`. A read is then tried again here, and `DatabaseStatus` shows that it is waiting.
export const busy = $state({ reads: 0 })

/** Whether an answer is the database being busy, as opposed to a request that failed. */
export const isBusy = (response, value) => response.status === 503 && value?.code === 'busy'

/** How many times a read is tried again: about three minutes, past most imports. */
const READ_TRIES = 10

/**
 * The wait before try `n` (from 0): the server's `Retry-After`, doubling from a second and capped at
 * 30 s, give or take a quarter, so readers who were turned away together do not return together.
 */
export function backoff(n, retryAfter = 0) {
  const base = Math.min(30000, Math.max(retryAfter * 1000, 1000 * 2 ** n))
  return base * (0.75 + Math.random() / 2)
}

export const retryAfter = response => Number(response.headers.get('retry-after')) || 0

/** Wait `ms`, or until `signal` aborts, which rejects as the fetch it stands for would. */
export function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason)
    const timer = setTimeout(() => { signal?.removeEventListener('abort', stop); resolve() }, ms)
    const stop = () => { clearTimeout(timer); reject(signal.reason) }
    signal?.addEventListener('abort', stop, { once: true })
  })
}

/**
 * Ask `answer()` again while the database is busy, starting from its first busy answer. Returns the
 * first answer that is not busy, or the last busy one once the tries run out.
 */
export async function waitOut(answer, first, signal) {
  let last = first
  busy.reads++
  try {
    for (let n = 0; n < READ_TRIES && isBusy(last.response, last.value); n++) {
      await pause(backoff(n, retryAfter(last.response)), signal)
      last = await answer()
    }
    return last
  } finally { busy.reads-- }
}
