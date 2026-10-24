// The write freeze. While `WRITES_PAUSED` is "1" (`wrangler secret put WRITES_PAUSED`), every request
// that would write to the database is answered 503 `paused`, and everything else reads as usual. A
// signed-in reader's session keeps reading: it is neither refreshed nor cleaned up while frozen.
// Signing out still works in the browser, and a sign-in that comes back from a provider is sent to
// the sign-in page with the reason.
import type { BetterAuthOptions } from 'better-auth';
import { getCookies } from 'better-auth/cookies';

/** How long a refused write is told to wait, in seconds. */
export const PAUSED_RETRY = 300;
export const PAUSED_MESSAGE = 'Changes are paused for now. Everything can still be read; try again later.';

export const writesPaused = (env: Env) => (env as unknown as Record<string, string | undefined>).WRITES_PAUSED === '1';

// A POST that writes nothing: an image search sends a vector and reads its neighbours.
const READ_POSTS = new Set(['/atlas/similar/query', '/api/contact']);
// The account routes a page reads with GET and that write nothing once the session is not refreshed.
// Every other one (a sign-in callback, a passkey challenge, a mailed link) writes.
const AUTH_READS = new Set(['/get-session', '/list-sessions', '/list-accounts', '/passkey/list-user-passkeys',
  '/admin/list-users', '/admin/get-user', '/ok', '/error', '/.well-known/openid-configuration']);

/** Whether a request writes to the database: every POST but a search, and the account routes that sign in. */
export function writes(method: string, path: string): boolean {
  if (method === 'GET' || method === 'HEAD') return path.startsWith('/api/auth/') && !AUTH_READS.has(path.slice('/api/auth'.length));
  return !READ_POSTS.has(path);
}

export const pausedResponse = (headers: Record<string, string> = {}) => Response.json(
  { error: 'paused', message: PAUSED_MESSAGE },
  { status: 503, headers: { ...headers, 'retry-after': String(PAUSED_RETRY), 'cache-control': 'no-store' } });

/**
 * A sign-out while paused: the browser's session cookies are expired, with the names and attributes
 * Better Auth gives them (the `__Secure-` prefix on https). The cached session may be split into
 * numbered cookies; each one sent goes. The session's row is not touched, so the session stays valid
 * until it expires or, once writes resume, is revoked from another device: a copy of its token taken
 * before the sign-out still signs in meanwhile. Only a page of this site may ask: a request whose
 * Origin is another one, or missing, is refused 403.
 */
export function pausedSignOut(request: Request, options: BetterAuthOptions): Response {
  if (request.headers.get('origin') !== new URL(request.url).origin)
    return Response.json({ error: 'forbidden', message: 'Sign out from this site.' }, { status: 403, headers: { 'cache-control': 'no-store' } });
  const { sessionToken, sessionData, dontRememberToken } = getCookies(options);
  const sent = (request.headers.get('cookie') ?? '').split(';').map(pair => pair.split('=')[0].trim());
  const chunks = sent.filter(name => name.startsWith(sessionData.name + '.') && /^\d+$/.test(name.slice(sessionData.name.length + 1)));
  const headers = new Headers({ 'content-type': 'application/json', 'cache-control': 'no-store' });
  for (const [name, attributes] of [[sessionToken.name, sessionToken.attributes], [sessionData.name, sessionData.attributes],
    ...chunks.map(name => [name, sessionData.attributes] as const), [dontRememberToken.name, dontRememberToken.attributes]] as const)
    headers.append('set-cookie', expired(name, attributes));
  return new Response(JSON.stringify({ success: true }), { headers });
}

function expired(name: string, attributes: { path?: string; domain?: string; httpOnly?: boolean; secure?: boolean; sameSite?: string | boolean }) {
  const sameSite = typeof attributes.sameSite === 'string' ? attributes.sameSite[0].toUpperCase() + attributes.sameSite.slice(1).toLowerCase() : null;
  return [`${name}=`, 'Max-Age=0', `Path=${attributes.path ?? '/'}`, attributes.domain && `Domain=${attributes.domain}`,
    attributes.httpOnly && 'HttpOnly', attributes.secure && 'Secure', sameSite && `SameSite=${sameSite}`].filter(Boolean).join('; ');
}

/** A provider's callback while paused goes to the sign-in page, which shows why it did not sign in. */
export const pausedCallback = (origin: string) => new Response(null, { status: 302,
  headers: { location: `${origin}/sign-in?error=paused`, 'cache-control': 'no-store' } });
