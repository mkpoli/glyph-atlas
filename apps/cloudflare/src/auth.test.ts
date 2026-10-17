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
const d1 = (db: Database) => ({ prepare: (sql: string) => new Statement(db, sql),
  batch: async (list: Statement[]) => { const results = []; for (const s of list) results.push(await s.all()); return results },
  exec: async (sql: string) => db.exec(sql) });

function setup() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  let code = '';
  const env = { DB: d1(db), BETTER_AUTH_SECRET: 'auth-test-secret-auth-test-secret-0123', MAIL_FROM: 'sign-in@example.org',
    EMAIL: { send: async (mail: { subject: string }) => { code = mail.subject.match(/\d{6}/)?.[0] ?? '' } } } as unknown as Env;
  const origin = 'https://glyphatlas.org', handler = auth(env, origin).handler, jar = new Map<string, string>();
  const call = async (path: string, body: unknown) => {
    const response = await handler(new Request(origin + '/api/auth' + path, { method: 'POST', body: JSON.stringify(body), headers: {
      origin, 'content-type': 'application/json', 'cf-connecting-ip': '192.0.2.10', cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; ') } }));
    for (const cookie of response.headers.getSetCookie()) { const [k, v] = cookie.split(';')[0].split(/=(.*)/); jar.set(k, v) }
    return response.status;
  };
  const image = (email: string) => (db.query('SELECT image FROM "user" WHERE email=?').get(email) as { image: string | null }).image;
  return { call, image, code: () => code };
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
