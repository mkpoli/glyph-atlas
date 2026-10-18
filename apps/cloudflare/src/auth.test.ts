import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { auth } from './auth';

// The auth library reads through D1's whole statement API, `raw` and `run`'s row id included.
class Statement {
  constructor(private db: Database, private sql: string, private args: any[] = []) {}
  bind(...args: any[]) { return new Statement(this.db, this.sql, args) }
  async all() { return { results: this.db.query(this.sql).all(...this.args), success: true, meta: {} } }
  async run() { const r = this.db.query(this.sql).run(...this.args); return { results: [], success: true, meta: { changes: r.changes, last_row_id: Number(r.lastInsertRowid) } } }
  async first(column?: string) { const row = this.db.query(this.sql).get(...this.args) as any; return column ? row?.[column] ?? null : row }
  async raw() { return this.db.query(this.sql).values(...this.args) }
}
const d1 = (db: Database, statements: string[] = []) => ({ prepare: (sql: string) => (statements.push(sql), new Statement(db, sql)),
  batch: async (list: Statement[]) => { const results = []; for (const s of list) results.push(await s.all()); return results },
  exec: async (sql: string) => db.exec(sql) });

function setup(origin = 'https://glyphatlas.org') {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  let code = '';
  const statements: string[] = [];
  // SIGN_IN_MAIL's two a minute, by key.
  const sent = new Map<string, number>();
  const env = { DB: d1(db, statements), BETTER_AUTH_SECRET: 'auth-test-secret-auth-test-secret-0123', MAIL_FROM: 'sign-in@example.org',
    EMAIL: { send: async (mail: { subject: string }) => { code = mail.subject.match(/\d{6}/)?.[0] ?? '' } },
    SIGN_IN_MAIL: { limit: async ({ key }: { key: string }) => { sent.set(key, (sent.get(key) ?? 0) + 1); return { success: sent.get(key)! <= 2 } } } } as unknown as Env;
  const handler = auth(env, origin).handler, jar = new Map<string, string>();
  const call = async (path: string, body: unknown, address = '192.0.2.10') => {
    const response = await handler(new Request(origin + '/api/auth' + path, { method: 'POST', body: JSON.stringify(body), headers: {
      origin, 'content-type': 'application/json', 'cf-connecting-ip': address, cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; ') } }));
    for (const cookie of response.headers.getSetCookie()) { const [k, v] = cookie.split(';')[0].split(/=(.*)/); jar.set(k, v) }
    return response.status;
  };
  const image = (email: string) => (db.query('SELECT image FROM "user" WHERE email=?').get(email) as { image: string | null }).image;
  return { call, image, code: () => code, statements };
}

describe('a reader\'s picture through the auth API', () => {
  it('is never taken from a sign-in by mail or a profile update', async () => {
    const { call, image, code } = setup();
    const email = 'new@example.org';
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' })).toBe(200);
    expect(await call('/sign-in/email-otp', { email, otp: code(), image: 'https://example.org/tracker.png' })).toBe(200);
    expect(image(email)).toBeNull();
    expect(await call('/update-user', { image: 'https://example.org/tracker.png' })).toBe(400);
    expect(await call('/update-user', { name: 'Ada' })).toBe(200);
    expect(image(email)).toBeNull();
  });
});

describe('the auth tables', () => {
  it('are not introspected on a request', async () => {
    // Auth instances are kept per origin, so this one is new and makes its first check here.
    const { call, statements } = setup('https://schema.glyphatlas.org');
    expect(await call('/email-otp/send-verification-otp', { email: 'reader@example.org', type: 'sign-in' })).toBe(200);
    expect(statements.length).toBeGreaterThan(0);
    expect(statements.filter(sql => /pragma_table_info|sqlite_master|PRAGMA/i.test(sql))).toEqual([]);
  });
});

describe('sign-in codes to one address', () => {
  it('are two a minute whichever network asks, and a refused request keeps the code sent', async () => {
    const { call, code } = setup('https://mail.glyphatlas.org');
    const email = 'Reader@Example.org';
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' }, '192.0.2.20')).toBe(200);
    expect(await call('/email-otp/send-verification-otp', { email: email.toLowerCase(), type: 'sign-in' }, '198.51.100.7')).toBe(200);
    const last = code();
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' }, '203.0.113.9')).toBe(429);
    expect(await call('/email-otp/send-verification-otp', { email: 'other@example.org', type: 'sign-in' }, '203.0.113.9')).toBe(200);
    expect(await call('/sign-in/email-otp', { email, otp: last }, '203.0.113.9')).toBe(200);
  });
});
