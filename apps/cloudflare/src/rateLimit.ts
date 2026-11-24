// The request limiter, called once before the site or the API reads anything (`entry.js`). One client
// address may make LIMIT counted requests in PERIOD seconds. Going over is a strike, and each strike
// locks the address out for longer (LOCKOUTS). A locked address is answered 429 from what the isolate
// and the edge cache hold, without a database or bucket read.
//
// Counted requests from one address in a minute, 13 to 16 November 2026, in Cloudflare's analytics:
// browsers peaked at 632 (a tablet), 507, 470, 305, 292 and 280, and several addresses a day passed 300.
// The project's own tools (`node`, headless Chrome, `Bun`, `glyph-atlas-check`) peaked at 1,100 to 1,280
// and send the pass. A counted request costs about 35 ms of CPU, its D1 rows and three to five R2 reads.
// In front of this, an edge rule refuses an address over 300 requests in 10 seconds for 10 seconds.
import { addressKey } from './reverse';
import { OPEN, opened } from './open';

/** Counted requests one address may make in PERIOD seconds. The `REQUESTS` binding in `wrangler.jsonc` repeats both. */
export const LIMIT = 900;
export const PERIOD = 60;
/** The seconds an address is locked out on its first, second and third strike, and on every later one. */
export const LOCKOUTS = [60, 600, 3600, 86400];
/** Strikes are forgotten this many seconds after the last lockout ends. */
export const FORGET = 86400;
/** The header a tool sends the secret `RATE_LIMIT_PASS` in to go uncounted. */
export const PASS_HEADER = 'x-rate-limit-pass';
// The seconds an isolate goes on what it last read of an address, and how many addresses it remembers.
// Shorter than the first lockout, so a lockout made by another isolate is read while it still holds.
const HELD_SECONDS = 30, HELD_ADDRESSES = 5000;

/** An address's strikes so far, and the time in milliseconds its last lockout ends. */
export type Strikes = { strikes: number; until: number };
export type Store = { read(key: string): Promise<Strikes | null>; write(key: string, record: Strikes, seconds: number): Promise<void> };
export type Limiter = { limit(options: { key: string }): Promise<{ success: boolean }> };
export type Tools = { limiter: Limiter; store: Store; now?: () => number; log?: (line: string) => void };

// The site's built files, crop images, font files, Cloudflare's own paths and the health check cost no
// query. A user's picture costs one row at most, and a sister site asks for a page of them in its reader's
// name, so a reader locked out here must not blank them there. Static files that exist are answered before the Worker runs.
const UNCOUNTED = ['/_app/', '/atlas/media/', '/atlas/fonts/', '/api/avatars/', '/cdn-cgi/'];
/** Whether a request to `path` counts against its address: every page and API answer that reads data. */
export const counted = (path: string) => path !== '/health' && !UNCOUNTED.some(prefix => path.startsWith(prefix));

/** Whether a request carries the pass. Without the secret set, none does. */
export function passes(sent: string | null, pass: string | undefined): boolean {
  if (!pass || !sent || sent.length !== pass.length) return false;
  let differing = 0;
  for (let i = 0; i < pass.length; i++) differing |= sent.charCodeAt(i) ^ pass.charCodeAt(i);
  return differing === 0;
}

/** An address as the log keeps it: an IPv4 address without its last number, an IPv6 one as its /48. */
export function shortened(key: string): string {
  if (key.endsWith('::/64')) return key.split(':').slice(0, 3).join(':') + '::/48';
  const numbers = key.split('.');
  return numbers.length === 4 ? `${numbers.slice(0, 3).join('.')}.0/24` : key;
}

/**
 * The limiter over one binding and one store. The function it returns answers the seconds an address
 * must wait, or null when its request may go on.
 *
 * The limiter's key carries the strike count, so an address let back in starts a fresh allowance
 * whatever the binding still counts from before its lockout.
 */
export function guard({ limiter, store, now = Date.now, log = console.log }: Tools) {
  const held = new Map<string, { record: Strikes | null; read: number }>();
  const striking = new Map<string, Promise<Strikes | null>>();
  const standing = (record: Strikes | null, at: number) => record && at < record.until + FORGET * 1000 ? record : null;
  const wait = (record: Strikes | null, at: number) => record && at < record.until ? Math.ceil((record.until - at) / 1000) : null;
  const hold = (key: string, record: Strikes | null, at: number) => {
    held.delete(key);
    if (held.size >= HELD_ADDRESSES) held.delete(held.keys().next().value!);
    held.set(key, { record, read: at });
    return record;
  };
  // What is known of an address: the isolate's copy while a lockout runs or the copy is recent, else the store's.
  async function known(key: string, at: number) {
    const mine = held.get(key);
    if (mine && (at < (mine.record?.until ?? 0) || at - mine.read < HELD_SECONDS * 1000)) return standing(mine.record, at);
    return hold(key, standing(await store.read(key), at), at);
  }
  // One strike for an address over its limit after `count` strikes. A burst arrives as many requests
  // at once, and they share the one strike. A lockout another isolate has made since stands as it is.
  async function strike(key: string, count: number, path: string) {
    const read = await store.read(key), at = now();
    const mine = standing(held.get(key)?.record ?? null, at), theirs = standing(read, at);
    const latest = theirs && (!mine || theirs.strikes > mine.strikes) ? theirs : mine;
    if (latest && latest.strikes > count) return hold(key, latest, at);
    const strikes = count + 1, seconds = LOCKOUTS[Math.min(strikes, LOCKOUTS.length) - 1];
    const record = hold(key, { strikes, until: at + seconds * 1000 }, at)!;
    log(JSON.stringify({ event: 'rate_limit_strike', address: shortened(key), strikes, seconds, path: path.slice(0, 200) }));
    await store.write(key, record, seconds + FORGET);
    return record;
  }
  return async (key: string, path: string): Promise<number | null> => {
    const at = now(), before = await known(key, at), locked = wait(before, at);
    if (locked) return locked;
    const count = before?.strikes ?? 0;
    if ((await limiter.limit({ key: `${key}|${count}` })).success) return null;
    let pending = striking.get(key);
    if (!pending) striking.set(key, pending = strike(key, count, path).finally(() => striking.delete(key)));
    return wait(await pending, now());
  };
}

// The strikes at the edge, in a cache of their own: the site answers any request from the default
// cache by its address, and a record kept there could be read by asking for it. A cache holds what
// one Cloudflare location wrote, so an address is locked out per location, as the binding counts.
const RECORDS = 'https://glyphatlas.org/_strikes/';
export const edge: Store = {
  async read(key) {
    const hit = await (await caches.open('strikes')).match(RECORDS + encodeURIComponent(key));
    if (!hit) return null;
    const { strikes, until } = await hit.json<Strikes>();
    return Number.isInteger(strikes) && Number.isFinite(until) ? { strikes, until } : null;
  },
  async write(key, record, seconds) {
    await (await caches.open('strikes')).put(RECORDS + encodeURIComponent(key),
      Response.json(record, { headers: { 'cache-control': `max-age=${seconds}` } }));
  },
};

// One limiter per binding, which is to say per isolate.
const guards = new WeakMap<Limiter, ReturnType<typeof guard>>();

/**
 * The 429 a request is refused with, or null when it may go on. A request that is not counted, or
 * carries the pass, goes on untouched. A limiter that fails lets the request through and logs it.
 */
export async function limited(request: Request, env: Env, store: Store = edge): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (!counted(path)) return null;
  if (passes(request.headers.get(PASS_HEADER), (env as unknown as Record<string, string | undefined>).RATE_LIMIT_PASS)) return null;
  let seconds: number | null;
  try {
    let check = guards.get(env.REQUESTS);
    if (!check) guards.set(env.REQUESTS, check = guard({ limiter: env.REQUESTS, store }));
    seconds = await check(addressKey(request.headers.get('cf-connecting-ip')), path);
  } catch (error) {
    console.error(JSON.stringify({ event: 'rate_limit_failed', path, message: String((error as Error)?.message ?? error).slice(0, 500) }));
    return null;
  }
  if (seconds === null) return null;
  return Response.json({ detail: 'Too many requests from this address. Try again later.', code: 'rate_limited' },
    { status: 429, headers: { ...(opened(path) ? OPEN : {}), 'retry-after': String(seconds), 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
}
