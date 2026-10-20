// The write freeze. While `WRITES_PAUSED` is "1" (`wrangler secret put WRITES_PAUSED`), every request
// that would write to the database is answered 503 `paused`, and everything else reads as usual. A
// signed-in reader's session keeps reading: it is neither refreshed nor cleaned up while frozen.

/** How long a refused write is told to wait, in seconds. */
export const PAUSED_RETRY = 300;
export const PAUSED_MESSAGE = 'Saving is paused while the site is under maintenance. Everything can still be read; try again later.';

export const writesPaused = (env: Env) => (env as unknown as Record<string, string | undefined>).WRITES_PAUSED === '1';

// A POST that writes nothing: an image search sends a vector and reads its neighbours.
const READ_POSTS = new Set(['/atlas/similar/query']);
// The account routes a page reads with GET and that write nothing once the session is not refreshed.
// Every other one (a sign-in callback, a passkey challenge, a mailed link) writes.
const AUTH_READS = new Set(['/get-session', '/list-sessions', '/list-accounts', '/passkey/list-user-passkeys',
  '/admin/list-users', '/admin/get-user', '/ok', '/error']);

/** Whether a request writes to the database: every POST but a search, and the account routes that sign in. */
export function writes(method: string, path: string): boolean {
  if (method === 'GET' || method === 'HEAD') return path.startsWith('/api/auth/') && !AUTH_READS.has(path.slice('/api/auth'.length));
  return !READ_POSTS.has(path);
}

export const pausedResponse = (headers: Record<string, string> = {}) => Response.json(
  { error: 'paused', message: PAUSED_MESSAGE },
  { status: 503, headers: { ...headers, 'retry-after': String(PAUSED_RETRY), 'cache-control': 'no-store' } });
