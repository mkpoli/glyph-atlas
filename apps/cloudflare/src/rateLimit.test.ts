import { describe, expect, it } from 'bun:test';
import { readFileSync } from 'node:fs';
import { FORGET, LIMIT, LOCKOUTS, PASS_HEADER, PERIOD, counted, guard, limited, passes, shortened, type Limiter, type Store, type Strikes } from './rateLimit';

const MINUTE = 60_000, HOUR = 60 * MINUTE;

// A clock the test moves, a limiter that counts each key in fixed windows of PERIOD seconds, and a
// store two isolates can share. Each counts its calls.
function world(limit = 3) {
  const state = { at: Date.UTC(2026, 10, 16), limits: 0, reads: 0, writes: [] as [string, Strikes, number][], lines: [] as any[], keys: [] as string[] };
  const windows = new Map<string, number>(), records = new Map<string, Strikes>();
  const limiter: Limiter = { async limit({ key }) {
    state.limits++; state.keys.push(key);
    const window = `${key} ${Math.floor(state.at / (PERIOD * 1000))}`, count = windows.get(window) ?? 0;
    if (count >= limit) return { success: false };
    windows.set(window, count + 1);
    return { success: true };
  } };
  const store: Store = {
    async read(key) { state.reads++; return records.get(key) ?? null },
    async write(key, record, seconds) { state.writes.push([key, record, seconds]); records.set(key, record) },
  };
  const isolate = () => guard({ limiter, store, now: () => state.at, log: line => state.lines.push(JSON.parse(line)) });
  return { state, limiter, store, records, isolate, check: isolate() };
}
// Ask until the limiter refuses; the refusal is the strike.
async function flood(check: ReturnType<typeof guard>, key = '192.0.2.7', path = '/layers/gallery') {
  for (let n = 0; n < 50; n++) { const wait = await check(key, path); if (wait !== null) return wait }
  throw new Error('never refused');
}

describe('only requests that read data are counted', () => {
  it('counts pages and API answers', () => {
    for (const path of ['/', '/en', '/ja/character/U+5019', '/en/gallery/__data.json', '/atlas', '/atlas/characters/3dh00000', '/atlas/ids/' + 'a'.repeat(64) + '.svg',
      '/layers/gallery', '/api/auth/get-session', '/api/account', '/sitemap.xml', '/wp-login.php', '/atlas/mediafile', '/healthz'])
      expect([path, counted(path)]).toEqual([path, true]);
  });
  it('leaves out built files, crop images, pictures of users, Cloudflare’s paths and the health check', () => {
    for (const path of ['/_app/immutable/chunks/a.js', '/_app/version.json', '/atlas/media/' + 'a'.repeat(64) + '.webp', '/cdn-cgi/rum', '/cdn-cgi/challenge-platform/x', '/api/avatars/00000000-0000-0000-0000-000000000001', '/health'])
      expect([path, counted(path)]).toEqual([path, false]);
  });
});

describe('the pass', () => {
  it('lets through only the secret itself, and nothing while no secret is set', () => {
    expect(passes('s3cret-pass', 's3cret-pass')).toBe(true);
    for (const [sent, pass] of [['s3cret-pasS', 's3cret-pass'], ['s3cret', 's3cret-pass'], ['s3cret-pass-and-more', 's3cret-pass'], [null, 's3cret-pass'],
      ['', ''], ['anything', undefined], [null, undefined], ['', undefined]] as const)
      expect([sent, pass, passes(sent, pass)]).toEqual([sent, pass, false]);
  });
});

describe('an address over its limit is locked out', () => {
  it('lets an address under its limit through on what the isolate holds', async () => {
    const { state, check } = world(10);
    for (let n = 0; n < 3; n++) expect(await check('192.0.2.7', '/atlas')).toBeNull();
    expect([state.limits, state.reads, state.writes.length, state.lines.length]).toEqual([3, 1, 0, 0]);
    // What the isolate read is asked again once it is half a minute old.
    state.at += 31_000;
    expect(await check('192.0.2.7', '/atlas')).toBeNull();
    expect(state.reads).toBe(2);
  });

  it('strikes once, answers the wait, and asks neither the limiter nor the store while locked', async () => {
    const { state, check } = world();
    expect(await flood(check)).toBe(60);
    expect(state.writes).toEqual([['192.0.2.7', { strikes: 1, until: state.at + MINUTE }, 60 + FORGET]]);
    expect(state.lines).toEqual([{ event: 'rate_limit_strike', address: '192.0.2.0/24', strikes: 1, seconds: 60, path: '/layers/gallery' }]);
    const before = [state.limits, state.reads];
    state.at += 20_500;
    for (let n = 0; n < 10; n++) expect(await check('192.0.2.7', '/atlas')).toBe(40);
    expect([state.limits, state.reads, state.writes.length, state.lines.length]).toEqual([...before, 1, 1]);
    // Another address is not held up.
    expect(await check('192.0.2.8', '/atlas')).toBeNull();
  });

  it('gives a fresh allowance after the lockout, whatever the binding still counts', async () => {
    const { state, check } = world();
    await flood(check);
    // One millisecond past the lockout the limiter's window may still hold the old count.
    state.at += MINUTE;
    for (let n = 0; n < 3; n++) expect(await check('192.0.2.7', '/atlas')).toBeNull();
    expect(state.keys.at(0)).toBe('192.0.2.7|0');
    expect(state.keys.at(-1)).toBe('192.0.2.7|1');
    expect(state.lines.length).toBe(1);
  });

  it('locks out for 1 minute, 10 minutes, 1 hour, then 24 hours each time', async () => {
    const { state, check } = world();
    const waits = [];
    for (let n = 0; n < 6; n++) { const wait = await flood(check); waits.push(wait); state.at += wait * 1000 }
    expect(waits).toEqual([60, 600, 3600, 86400, 86400, 86400]);
    expect(waits.slice(0, 4)).toEqual(LOCKOUTS);
    expect(state.lines.map(line => [line.strikes, line.seconds])).toEqual([[1, 60], [2, 600], [3, 3600], [4, 86400], [5, 86400], [6, 86400]]);
  });

  it('forgets the strikes 24 hours after the last lockout ends', async () => {
    const { state, check } = world();
    expect(await flood(check)).toBe(60);
    state.at += MINUTE + 24 * HOUR - 1000;
    expect(await flood(check)).toBe(600);
    state.at += 10 * MINUTE + 24 * HOUR;
    expect(await flood(check)).toBe(60);
    expect(state.lines.map(line => line.strikes)).toEqual([1, 2, 1]);
  });

  it('counts a burst that arrives at once as one strike', async () => {
    const { state, check } = world();
    const waits = await Promise.all(Array.from({ length: 40 }, () => check('192.0.2.7', '/atlas')));
    expect(waits.filter(wait => wait === null).length).toBe(3);
    expect(new Set(waits.filter(wait => wait !== null))).toEqual(new Set([60]));
    expect([state.writes.length, state.lines.length]).toEqual([1, 1]);
  });

  it('holds to a lockout another isolate made', async () => {
    const { state, isolate, check } = world();
    const other = isolate();
    expect(await other('192.0.2.7', '/atlas')).toBeNull();
    await flood(check);
    // The other isolate last read the address as clear; the limiter refuses it, and it reads the lockout.
    state.at += 5000;
    expect(await other('192.0.2.7', '/atlas')).toBe(55);
    // A new isolate reads it before asking the limiter.
    const limits = state.limits;
    expect(await isolate()('192.0.2.7', '/atlas')).toBe(55);
    expect([state.limits, state.writes.length, state.lines.length]).toEqual([limits, 1, 1]);
  });

  it('never logs a whole address', () => {
    expect(shortened('192.0.2.77')).toBe('192.0.2.0/24');
    expect(shortened('2001:db8:12:3456::/64')).toBe('2001:db8:12::/48');
    expect(shortened('local')).toBe('local');
  });
});

describe('the Worker refuses before it reads anything', () => {
  const env = (limiter: Limiter, pass?: string) => ({ REQUESTS: limiter, RATE_LIMIT_PASS: pass }) as unknown as Env;
  // The Worker's own limiter logs to the console; the tests keep what it writes.
  const quietly = async (run: (logged: any[]) => Promise<void>) => {
    const { log, error } = console, logged: any[] = [];
    console.log = console.error = (line: string) => { logged.push(JSON.parse(line)) };
    try { await run(logged) } finally { Object.assign(console, { log, error }) }
  };
  const ask = (path: string, headers: Record<string, string> = {}) => new Request(`https://glyphatlas.org${path}`, { headers: { 'cf-connecting-ip': '2001:db8:0:1:aaaa:bbbb:cccc:dddd', ...headers } });

  it('answers 429 with the wait and no caching, keyed by the address’s /64', () => quietly(async logged => {
    const { state, limiter, store } = world(2);
    const site = env(limiter);
    for (let n = 0; n < 2; n++) expect(await limited(ask('/atlas'), site, store)).toBeNull();
    const refused = (await limited(ask('/en/gallery', { 'cf-connecting-ip': '2001:db8:0:1::2' }), site, store))!;
    expect([refused.status, refused.headers.get('retry-after'), refused.headers.get('cache-control')]).toEqual([429, '60', 'no-store']);
    expect(await refused.json()).toEqual({ detail: 'Too many requests from this address. Try again later.', code: 'rate_limited' });
    expect(state.writes[0][0]).toBe('2001:db8:0:1::/64');
    expect(logged).toEqual([{ event: 'rate_limit_strike', address: '2001:db8:0::/48', strikes: 1, seconds: 60, path: '/en/gallery' }]);
  }));

  it('lets another site read the refusal of a route it may read', () => quietly(async () => {
    const { limiter, store } = world(0);
    const site = env(limiter);
    for (const path of ['/atlas/documents/hk:1/characters', '/atlas/cite/crop/3dh00000', '/atlas/composer/v1/manifest.json'])
      expect([path, (await limited(ask(path), site, store))!.headers.get('access-control-allow-origin')]).toEqual([path, '*']);
    for (const path of ['/atlas', '/en', '/api/account'])
      expect([path, (await limited(ask(path), site, store))!.headers.get('access-control-allow-origin')]).toEqual([path, null]);
  }));

  it('lets an uncounted path and a request with the pass through without asking the limiter', () => quietly(async () => {
    const { state, limiter, store } = world(0);
    const site = env(limiter, 's3cret-pass');
    expect(await limited(ask('/atlas/media/' + 'a'.repeat(64) + '.webp'), site, store)).toBeNull();
    expect(await limited(ask('/health'), site, store)).toBeNull();
    expect(await limited(ask('/atlas', { [PASS_HEADER]: 's3cret-pass' }), site, store)).toBeNull();
    expect([state.limits, state.reads]).toEqual([0, 0]);
    expect((await limited(ask('/atlas', { [PASS_HEADER]: 'wrong' }), site, store))!.status).toBe(429);
    // Without the secret set, the header opens nothing.
    const { limiter: other, store: empty } = world(0);
    expect((await limited(ask('/atlas', { [PASS_HEADER]: 's3cret-pass' }), env(other), empty))!.status).toBe(429);
  }));

  it('lets a request through when the limiter or the store fails, and logs it', () => quietly(async logged => {
    const { store } = world();
    expect(await limited(ask('/atlas'), env({ limit: async () => { throw new Error('down') } }), store)).toBeNull();
    expect(await limited(ask('/atlas'), env({ limit: async () => ({ success: true }) }), { ...store, read: async () => { throw new Error('down') } })).toBeNull();
    expect(await limited(ask('/atlas'), {} as Env, store)).toBeNull();
    expect(logged.map(line => [line.event, line.path])).toEqual(Array(3).fill(['rate_limit_failed', '/atlas']));
  }));

  it('is bound to a limiter with the limit and period named here', () => {
    const config = readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8');
    const [, limit, period] = config.match(/"name": "REQUESTS", "namespace_id": "\d+", "simple": \{"limit": (\d+), "period": (\d+)\}/)!;
    expect([Number(limit), Number(period)]).toEqual([LIMIT, PERIOD]);
  });
});
