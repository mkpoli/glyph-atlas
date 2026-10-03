// While an import runs (`wrangler d1 execute --file`), D1 queues every other query behind it, and a
// query that waits too long fails. These are the messages D1 documents for a database that is busy or
// restarting and will answer the same query once it is free again
// (https://developers.cloudflare.com/d1/observability/debug-d1/#error-list and
// https://developers.cloudflare.com/d1/best-practices/retry-queries/). A local database answers
// `database is locked` instead. A query that runs out of D1's memory or CPU time is left out: it
// fails the same way when asked again.
const TRANSIENT = new RegExp([
  'Network connection lost', 'storage caused object to be reset', 'reset because its code was updated',
  'D1 DB is overloaded', 'Cannot resolve D1 DB due to transient issue', 'Internal error while starting up D1 DB storage',
  'D1 DB storage operation exceeded timeout', 'SQLITE_BUSY', 'database is locked',
].map(text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'));

/** Whether an error is D1 being busy or restarting, judged by it and the errors it was caused by. */
export function transient(error: unknown): boolean {
  for (let e: any = error, depth = 0; e && depth < 5; e = e.cause, depth++) {
    if (TRANSIENT.test(String(e?.message ?? e))) return true;
    // Better Auth reports a session it could not read from D1 as its own 500 and logs D1's error
    // itself; the session table is only ever unreadable while the database is.
    if (e?.name === 'APIError' && e.statusCode >= 500 && e.body?.code === 'FAILED_TO_GET_SESSION') return true;
  }
  return false;
}

/** What the log keeps of an error: its name and message and those of its causes, never sent to a client. */
export function described(error: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  let e: any = error;
  for (let depth = 0; e && depth < 3; e = e.cause, depth++) {
    const at = depth ? `cause${depth}_` : '';
    out[at + 'error'] = e instanceof Error ? e.name : typeof e;
    out[at + 'message'] = String(e?.message ?? e).slice(0, 500);
  }
  return out;
}

/** How long a read may keep trying while D1 is busy, counted from its first attempt. */
export const READ_BUDGET = 4000;
/** The seconds a busy answer asks a client to wait before trying again. */
export const RETRY_AFTER = 5;

/**
 * Run `attempt` until it answers, retrying a transient error after a short jittered wait while the
 * budget lasts. Anything else, and a transient error once the budget is spent, is returned with the
 * number of attempts made.
 */
export async function retried<T>(attempt: () => Promise<T>, budget: number,
  sleep = (ms: number) => new Promise(done => setTimeout(done, ms))): Promise<{ value: T; attempts: number } | { error: unknown; attempts: number }> {
  const started = Date.now();
  for (let n = 1; ; n++) {
    try { return { value: await attempt(), attempts: n } }
    catch (error) {
      // 150 ms, 300 ms, 600 ms…, each somewhere between half and all of that.
      const wait = 150 * 2 ** (n - 1) * (0.5 + Math.random() / 2);
      if (!transient(error) || Date.now() - started + wait >= budget) return { error, attempts: n };
      await sleep(wait);
    }
  }
}
