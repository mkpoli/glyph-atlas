import { t } from './i18n.svelte.js'

// While the site's database takes an import, the Worker answers 503 with `code: 'busy'` and a
// `Retry-After`. A read is then tried again here, and `DatabaseStatus` shows that it is waiting.
export const busy = $state({ reads: 0 })

/** Whether an answer is the database being busy, as opposed to a request that failed. */
export const isBusy = (response, value) => response.status === 503 && value?.code === 'busy'

/** Whether an answer is the site refusing writes for a while (`WRITES_PAUSED`); reads still work. */
export const isPaused = (response, value) => response.status === 503 && value?.error === 'paused'

/** Whether an answer is a save from a page opened before a site update, naming a crop the site no
 *  longer knows by that name (409 `reload`). */
export const isReload = (response, value) => response.status === 409 && value?.code === 'reload'

/**
 * `fetch`, with a refused write's message in the reader's language, for clients that show `message`.
 * The refusal also carries `code: 'paused'` and the `Retry-After` as `retry_after`, since such a
 * client passes on the body and not the headers.
 */
export async function pausedFetch(input, init) {
  const response = await fetch(input, init)
  if (response.status !== 503) return response
  const value = await response.clone().json().catch(() => null)
  if (!isPaused(response, value)) return response
  return new Response(JSON.stringify({ ...value, code: 'paused', retry_after: retryAfter(response), message: t('client.paused') }),
    { status: response.status, statusText: response.statusText, headers: response.headers })
}

/** How many times a read is tried again: about three minutes, past most imports. */
const READ_TRIES = 10

/**
 * The wait before try `n` (from 0): doubling from a second and capped at 30 s, give or take a quarter,
 * so readers who were turned away together do not return together. The server's `Retry-After` (in
 * seconds) is a floor, uncapped: a try comes up to a quarter after it, never before.
 */
export function backoff(n, retryAfter = 0) {
  const doubling = Math.min(30000, 1000 * 2 ** n) * (0.75 + Math.random() / 2)
  return Math.max(doubling, retryAfter * 1000 * (1 + Math.random() / 4))
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
