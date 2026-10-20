import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import worker from './index';
import { PAUSED_MESSAGE, writes } from './freeze';

class Statement {
  constructor(private db: Database, private sql: string, private args: any[] = []) {}
  bind(...args: any[]) { return new Statement(this.db, this.sql, args) }
  async all() { return { results: this.db.query(this.sql).all(...this.args), success: true, meta: {} } }
  async run() { const r = this.db.query(this.sql).run(...this.args); return { results: [], success: true, meta: { changes: r.changes, last_row_id: Number(r.lastInsertRowid) } } }
  async first(column?: string) { const row = this.db.query(this.sql).get(...this.args) as any; return column ? row?.[column] ?? null : row }
  async raw() { return this.db.query(this.sql).values(...this.args) }
}
const d1 = (db: Database, statements: string[]) => ({ prepare: (sql: string) => (statements.push(sql), new Statement(db, sql)),
  batch: async (list: Statement[]) => { const results = []; for (const s of list) results.push(await s.all()); return results },
  exec: async (sql: string) => db.exec(sql) });

const ctx = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;
// Auth instances are kept per origin, each holding its own database, so every setup has its own.
let setups = 0;
function setup() {
  const ORIGIN = `https://freeze${++setups}.glyphatlas.org`;
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  db.exec(`INSERT INTO metadata(key,value) VALUES('published_at','"2026-10-01"')`);
  const statements: string[] = [];
  const env = (paused?: string) => ({ DB: d1(db, statements), BETTER_AUTH_SECRET: 'freeze-test-secret-freeze-test-secret-01', MAIL_FROM: 'sign-in@example.org',
    CATALOGUE_LAG: '60', ...(paused === undefined ? {} : { WRITES_PAUSED: paused }) }) as unknown as Env;
  const jar = new Map<string, string>();
  const send = async (paused: string | undefined, path: string, method = 'GET', body?: unknown, origin: string | null = ORIGIN) => {
    const response = await worker.fetch(new Request(ORIGIN + path, { method, body: body === undefined ? undefined : JSON.stringify(body), headers: {
      ...(origin === null ? {} : { origin }), 'content-type': 'application/json', 'cf-connecting-ip': '192.0.2.10', cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; ') } }), env(paused), ctx);
    for (const cookie of response.headers.getSetCookie()) { const [k, v] = cookie.split(';')[0].split(/=(.*)/); jar.set(k, v) }
    return response;
  };
  return { db, statements, send };
}
const written = (statements: string[]) => statements.filter(sql => /^\s*(INSERT|UPDATE|DELETE|REPLACE)\b/i.test(sql));

describe('the write freeze', () => {
  it('answers a write 503 paused with a retry time', async () => {
    const { send, statements } = setup();
    for (const [path, body] of [['/atlas/rounds', { items: [] }], ['/api/favourites', { crop: 'x', favourite: true }],
      ['/api/auth/sign-in/anonymous', {}], ['/api/admin/reject', {}]] as const) {
      const response = await send('1', path, 'POST', body);
      expect(response.status).toBe(503);
      expect(response.headers.get('retry-after')).toBe('300');
      expect(await response.json()).toEqual({ error: 'paused', message: PAUSED_MESSAGE });
    }
    expect(written(statements)).toEqual([]);
  });
  it('lets a read through', async () => {
    const { send } = setup();
    const response = await send('1', '/health');
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, published_at: '2026-10-01' });
  });
  it('lets a write through when unset or not "1"', async () => {
    for (const paused of [undefined, '0', '']) {
      const { send } = setup();
      // Past the freeze, a write without a session is asked to sign in.
      const response = await send(paused, '/atlas/rounds', 'POST', { items: [] });
      expect(response.status).toBe(401);
    }
  });
  it('keeps a signed-in reader reading, without refreshing the session', async () => {
    const { db, send, statements } = setup();
    expect((await send(undefined, '/api/auth/sign-in/anonymous', 'POST', {})).status).toBe(200);
    // A session a day past its update age, which a read would otherwise extend.
    db.exec(`UPDATE session SET expiresAt='${new Date(Date.now() + 80 * 86400e3).toISOString()}'`);
    const before = db.query('SELECT expiresAt FROM session').get();
    statements.length = 0;
    const account = await send('1', '/api/account');
    expect(account.status).toBe(200);
    expect((await account.json()).user?.anonymous).toBe(true);
    const session = await send('1', '/api/auth/get-session?disableCookieCache=true');
    expect(session.status).toBe(200);
    expect((await session.json())?.user).toBeTruthy();
    expect(written(statements)).toEqual([]);
    expect(db.query('SELECT expiresAt FROM session').get()).toEqual(before);
  });
  it('signs out in the browser by expiring the session cookies, writing nothing', async () => {
    const { db, send, statements } = setup();
    expect((await send(undefined, '/api/auth/sign-in/anonymous', 'POST', {})).status).toBe(200);
    statements.length = 0;
    const response = await send('1', '/api/auth/sign-out', 'POST', {});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true });
    const cookies = response.headers.getSetCookie();
    for (const name of ['__Secure-better-auth.session_token', '__Secure-better-auth.session_data', '__Secure-better-auth.dont_remember'])
      expect(cookies).toContain(`${name}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax`);
    expect(written(statements)).toEqual([]);
    expect(db.query('SELECT count(*) AS n FROM session').get()).toEqual({ n: 1 });
    // The browser, holding no cookie now, is signed out.
    expect(await (await send('1', '/api/auth/get-session')).json()).toBeNull();
  });
  it('refuses a paused sign-out from another origin, or with none', async () => {
    const { send, statements } = setup();
    expect((await send(undefined, '/api/auth/sign-in/anonymous', 'POST', {})).status).toBe(200);
    statements.length = 0;
    for (const origin of ['https://elsewhere.example', null]) {
      const response = await send('1', '/api/auth/sign-out', 'POST', {}, origin);
      expect(response.status).toBe(403);
      expect(response.headers.getSetCookie()).toEqual([]);
    }
    expect(written(statements)).toEqual([]);
    // The browser is still signed in.
    expect((await (await send('1', '/api/auth/get-session')).json())?.user).toBeTruthy();
  });
  it('sends a provider callback to the sign-in page with the reason', async () => {
    const { send, statements } = setup();
    const response = await send('1', '/api/auth/callback/github?code=x&state=y');
    expect(response.status).toBe(302);
    expect(new URL(response.headers.get('location')!).pathname + new URL(response.headers.get('location')!).search).toBe('/sign-in?error=paused');
    expect(written(statements)).toEqual([]);
  });
  it('counts sign-in routes read by GET as writes, and the session read as a read', () => {
    expect(writes('GET', '/api/auth/callback/github')).toBe(true);
    expect(writes('GET', '/api/auth/passkey/generate-register-options')).toBe(true);
    expect(writes('GET', '/api/auth/get-session')).toBe(false);
    expect(writes('GET', '/atlas/history')).toBe(false);
    expect(writes('POST', '/atlas/similar/query')).toBe(false);
    expect(writes('POST', '/atlas/forms/decisions')).toBe(true);
  });
});
