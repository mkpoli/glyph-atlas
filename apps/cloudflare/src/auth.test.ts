import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { auth, claim, viewer } from './auth';
import { hourly as run } from './accounts';

// The auth library reads through D1's whole statement API, `raw` and `run`'s row id included.
class Statement {
  constructor(private db: Database, readonly sql: string, private args: any[] = []) {}
  bind(...args: any[]) { return new Statement(this.db, this.sql, args) }
  async all() { return { results: this.db.query(this.sql).all(...this.args), success: true, meta: {} } }
  async run() { const r = this.db.query(this.sql).run(...this.args); return { results: [], success: true, meta: { changes: r.changes, last_row_id: Number(r.lastInsertRowid) } } }
  async first(column?: string) { const row = this.db.query(this.sql).get(...this.args) as any; return column ? row?.[column] ?? null : row }
  async raw() { return this.db.query(this.sql).values(...this.args) }
}
// A batch runs in one transaction, as D1 runs it.
const d1 = (db: Database, statements: string[] = []) => ({ prepare: (sql: string) => (statements.push(sql), new Statement(db, sql)),
  batch: async (list: Statement[]) => db.transaction(() => list.map(s => /^\s*SELECT/i.test(s.sql)
    ? { results: db.query(s.sql).all(...(s as any).args), success: true, meta: {} }
    : { results: [], success: true, meta: { changes: db.query(s.sql).run(...(s as any).args).changes } }))(),
  exec: async (sql: string) => db.exec(sql) });

/** An empty database built from one migrations directory, foreign keys on as D1 has them. */
export function migrated(dir: string) {
  const db = new Database(':memory:');
  db.exec('PRAGMA foreign_keys=ON');
  const migrations = new URL(`../${dir}/`, import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}

function setup(origin = 'https://glyphatlas.org', accounts = migrated('migrations-accounts')) {
  const atlas = migrated('migrations-atlas');
  let code = '';
  const statements: string[] = [];
  // SIGN_IN_MAIL's two a minute, by key.
  const sent = new Map<string, number>();
  // MEDIA's objects by key, listed by prefix a page of two at a time.
  const media = new Map<string, string>();
  const MEDIA = {
    list: async ({ prefix, cursor }: { prefix: string; cursor?: string }) => {
      // As in R2, the cursor stands after the last key listed.
      const keys = [...media.keys()].filter(key => key.startsWith(prefix) && key > (cursor ?? '')).sort();
      return { objects: keys.slice(0, 2).map(key => ({ key })), truncated: keys.length > 2, cursor: keys[1] };
    },
    delete: async (keys: string | string[]) => { for (const key of [keys].flat()) media.delete(key) },
  };
  const env = { ACCOUNTS: d1(accounts, statements), ATLAS: d1(atlas), MEDIA, BETTER_AUTH_SECRET: 'auth-test-secret-auth-test-secret-0123', MAIL_FROM: 'sign-in@example.org',
    EMAIL: { send: async (mail: { subject: string }) => { code = mail.subject.match(/\d{6}/)?.[0] ?? '' } },
    SIGN_IN_MAIL: { limit: async ({ key }: { key: string }) => { sent.set(key, (sent.get(key) ?? 0) + 1); return { success: sent.get(key)! <= 2 } } } } as unknown as Env;
  const handler = auth(env, origin).handler, jar = new Map<string, string>();
  const send = async (path: string, body: unknown, address = '192.0.2.10') => {
    const response = await handler(new Request(origin + '/api/auth' + path, { method: body === undefined ? 'GET' : 'POST',
      body: body === undefined ? undefined : JSON.stringify(body), headers: {
      origin, 'content-type': 'application/json', 'cf-connecting-ip': address, cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; ') } }));
    for (const cookie of response.headers.getSetCookie()) { const [k, v] = cookie.split(';')[0].split(/=(.*)/); if (v) jar.set(k, v); else jar.delete(k) }
    return response;
  };
  const call = async (path: string, body: unknown, address?: string) => (await send(path, body, address)).status;
  const image = (email: string) => (accounts.query('SELECT image FROM "user" WHERE email=?').get(email) as { image: string | null }).image;
  // Signs in by a mailed code, as a new browser.
  const signIn = async (email: string) => {
    jar.clear();
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' })).toBe(200);
    expect(await call('/sign-in/email-otp', { email, otp: code })).toBe(200);
    return (accounts.query('SELECT id FROM "user" WHERE email=?').get(email) as { id: string }).id;
  };
  return { env, accounts, atlas, media, call, send, jar, image, signIn, code: () => code, statements };
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

describe('requests that mail no sign-in code', () => {
  it('leave the address its two a minute', async () => {
    const { call } = setup('https://refused.glyphatlas.org');
    const email = 'reader@example.org';
    expect(await call('/email-otp/send-verification-otp', { email, type: 'forget-password' }, '192.0.2.1')).toBe(400);
    expect(await call('/email-otp/send-verification-otp', { email, type: 'change-email' }, '192.0.2.2')).toBe(400);
    expect(await call('/email-otp/send-verification-otp', { email: ' ' + email, type: 'sign-in' }, '192.0.2.3')).toBe(400);
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' }, '192.0.2.4')).toBe(200);
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' }, '192.0.2.5')).toBe(200);
  });
  it('find the other routes that mail a code off', async () => {
    const { call } = setup('https://routes.glyphatlas.org');
    const email = 'reader@example.org';
    expect(await call('/email-otp/send-verification-otp', { email, type: 'sign-in' })).toBe(200);
    for (const path of ['/email-otp/request-password-reset', '/forget-password/email-otp', '/email-otp/reset-password',
      '/email-otp/request-email-change', '/email-otp/change-email'])
      expect(await call(path, { email, newEmail: 'other@example.org', otp: '000000', password: 'x'.repeat(12) })).toBe(404);
  });
});

describe('users and what they wrote, in two databases', () => {
  it('give a new user their own actor in ATLAS, and keep the auth tables out of it', async () => {
    const { atlas, accounts, signIn } = setup('https://actors.glyphatlas.org');
    const id = await signIn('ada@example.org');
    expect(atlas.query('SELECT key,user,via,typeof(at) AS at FROM actors').all()).toEqual([{ key: id, user: id, via: 'account', at: 'integer' }]);
    expect(atlas.query(`SELECT name FROM sqlite_master WHERE name IN ('user','session','account')`).all()).toEqual([]);
    expect(accounts.query(`SELECT name FROM sqlite_master WHERE name IN ('actors','favourites')`).all()).toEqual([]);
  });
  it('bring an anonymous user\'s actors and stars to the account it signs in to', async () => {
    const { atlas, accounts, call, jar, code, signIn } = setup('https://link.glyphatlas.org');
    const kept = await signIn('kept@example.org');
    atlas.exec(`INSERT INTO favourites VALUES('${kept}',7,1),('${kept}',8,1)`);
    // A browser signed out: an anonymous session, its stars, then a sign-in to the account.
    jar.clear();
    expect(await call('/sign-in/anonymous', {})).toBe(200);
    const anon = (accounts.query('SELECT id FROM "user" WHERE isAnonymous=1').get() as { id: string }).id;
    atlas.exec(`INSERT INTO favourites VALUES('${anon}',8,2),('${anon}',9,2)`);
    expect(await call('/email-otp/send-verification-otp', { email: 'kept@example.org', type: 'sign-in' })).toBe(200);
    expect(await call('/sign-in/email-otp', { email: 'kept@example.org', otp: code() })).toBe(200);
    expect(atlas.query('SELECT key,user FROM actors ORDER BY id').all()).toEqual([{ key: kept, user: kept }, { key: anon, user: kept }]);
    expect(atlas.query('SELECT user,crop,at FROM favourites ORDER BY crop').all()).toEqual([
      { user: kept, crop: 7, at: 1 }, { user: kept, crop: 8, at: 1 }, { user: kept, crop: 9, at: 2 }]);
  });
  it('bring an anonymous user\'s old reviewer ids only up to the account\'s five, the earliest first', async () => {
    const { atlas, accounts, call, jar, code, signIn } = setup('https://cap.glyphatlas.org');
    const kept = await signIn('kept@example.org');
    atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-00000001','${kept}','legacy',1),('reviewer-00000002','${kept}','legacy',1),('reviewer-00000003','${kept}','legacy',1)`);
    jar.clear();
    expect(await call('/sign-in/anonymous', {}, '192.0.2.11')).toBe(200);
    const anon = (accounts.query('SELECT id FROM "user" WHERE isAnonymous=1').get() as { id: string }).id;
    atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-0000000c','${anon}','legacy',30),('reviewer-0000000a','${anon}','legacy',10),('reviewer-0000000b','${anon}','legacy',20)`);
    expect(await call('/email-otp/send-verification-otp', { email: 'kept@example.org', type: 'sign-in' }, '192.0.2.11')).toBe(200);
    expect(await call('/sign-in/email-otp', { email: 'kept@example.org', otp: code() }, '192.0.2.11')).toBe(200);
    expect(atlas.query("SELECT key,user FROM actors WHERE key LIKE 'reviewer-0000000_' AND key>'reviewer-00000009' ORDER BY key").all()).toEqual([
      { key: 'reviewer-0000000a', user: kept }, { key: 'reviewer-0000000b', user: kept }, { key: 'reviewer-0000000c', user: anon }]);
    expect(atlas.query("SELECT count(*) AS n FROM actors WHERE user=? AND via='legacy'").get(kept)).toEqual({ n: 5 });
    expect(atlas.query('SELECT user FROM actors WHERE key=?').get(anon)).toEqual({ user: kept });
  });
  it('let a user take a name shaped like an old reviewer id only when they hold that id', async () => {
    const { atlas, call, signIn } = setup('https://names.glyphatlas.org');
    const id = await signIn('ada@example.org');
    expect(await call('/update-user', { name: 'reviewer-0000000a' })).toBe(400);
    atlas.prepare(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-0000000a',?,'legacy',1)`).run(id);
    expect(await call('/update-user', { name: 'reviewer-0000000a' })).toBe(200);
  });
});

describe('a session', () => {
  it('reads the same after the auth tables are copied to another database', async () => {
    const before = setup('https://copy.glyphatlas.org');
    const id = await before.signIn('ada@example.org');
    // The cutover copy: the same rows in a database built from migrations-accounts.
    const copy = migrated('migrations-accounts');
    for (const table of ['user', 'session', 'account', 'verification', 'rateLimit', 'passkey', 'account_profiles']) {
      const rows = before.accounts.query(`SELECT * FROM "${table}"`).all() as Record<string, unknown>[];
      for (const row of rows) copy.prepare(`INSERT INTO "${table}"(${Object.keys(row).map(k => `"${k}"`).join(',')}) VALUES(${Object.keys(row).map(() => '?').join(',')})`)
        .run(...(Object.values(row) as any[]));
    }
    const after = setup('https://copy2.glyphatlas.org', copy);
    const cookie = [...before.jar].filter(([k]) => !k.includes('session_data')).map(([k, v]) => `${k}=${v}`).join('; ');
    const me = await viewer(after.env, new Request('https://copy2.glyphatlas.org/api/account', { headers: { cookie } }), true);
    expect(me?.id).toBe(id);
  });
});

describe('deleting a user', () => {
  async function seeded(origin: string) {
    const s = setup(origin);
    const gone = await s.signIn('gone@example.org'), stays = await s.signIn('stays@example.org');
    const at = '2026-10-20T00:00:00.000Z';
    s.accounts.exec(`INSERT INTO account(id,accountId,providerId,userId,createdAt,updatedAt) VALUES('a1','583231','github','${gone}','${at}','${at}');
      INSERT INTO account_profiles(account,provider,handle,at) VALUES('a1','github','octocat','${at}');
      INSERT INTO passkey(id,publicKey,userId,credentialID,counter,deviceType,backedUp) VALUES('p1','k','${gone}','c',0,'singleDevice',0);`);
    s.atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-0000000a','${gone}','legacy',1);
      INSERT INTO favourites VALUES('${gone}',7,1),('${stays}',7,1);`);
    const admin = await s.signIn('admin@example.org');
    s.accounts.exec(`UPDATE "user" SET role='admin' WHERE id='${admin}'`);
    for (const key of [...s.jar.keys()]) if (key.includes('session_data')) s.jar.delete(key);
    const rows = (user: string) => Object.fromEntries(['session', 'account', 'passkey'].map(t =>
      [t, (s.accounts.query(`SELECT count(*) AS n FROM "${t}" WHERE userId=?`).get(user) as { n: number }).n]));
    return { ...s, gone, stays, rows };
  }
  it('removes its rows in ACCOUNTS, then its stars and actor links in ATLAS, and keeps the actors', async () => {
    const { call, accounts, atlas, env, gone, stays, rows } = await seeded('https://delete.glyphatlas.org');
    expect(rows(gone)).toEqual({ session: 1, account: 1, passkey: 1 });
    expect(await call('/admin/remove-user', { userId: gone })).toBe(200);
    expect(accounts.query('SELECT count(*) AS n FROM "user" WHERE id=?').get(gone)).toEqual({ n: 0 });
    expect(rows(gone)).toEqual({ session: 0, account: 0, passkey: 0 });
    expect(accounts.query('SELECT count(*) AS n FROM account_profiles').get()).toEqual({ n: 0 });
    expect(accounts.query('SELECT count(*) AS n FROM forgotten').get()).toEqual({ n: 0 });
    expect(atlas.query('SELECT user,crop FROM favourites').all()).toEqual([{ user: stays, crop: 7 }]);
    expect(atlas.query(`SELECT key,user FROM actors WHERE key IN (?, 'reviewer-0000000a') ORDER BY key`).all(gone))
      .toEqual([{ key: gone, user: null }, { key: 'reviewer-0000000a', user: null }]);
    expect(rows(stays).session).toBe(1);
    // The deleted user's old reviewer id is nobody's to claim.
    const other = { id: stays, name: 'stays', image: null, anonymous: false, admin: false };
    expect((await claim(env, other, 'reviewer-0000000a')).status).toBe(409);
  });
  it('deletes its uploaded pictures and the anonymous users joined to it, with their sessions', async () => {
    const { call, send, jar, code, accounts, atlas, media, gone, stays, rows } = await seeded('https://joined.glyphatlas.org');
    for (const name of ['a', 'b', 'c']) media.set(`avatars/${gone}/${name}.webp`, name);
    media.set(`avatars/${stays}/a.webp`, 'a');
    // An anonymous session, then a sign-in to the account that is deleted.
    const admin = new Map(jar);
    jar.clear();
    expect(await call('/sign-in/anonymous', {}, '192.0.2.11')).toBe(200);
    const anon = (accounts.query('SELECT id FROM "user" WHERE isAnonymous=1').get() as { id: string }).id;
    expect(await call('/email-otp/send-verification-otp', { email: 'gone@example.org', type: 'sign-in' }, '192.0.2.11')).toBe(200);
    expect(await call('/sign-in/email-otp', { email: 'gone@example.org', otp: code() }, '192.0.2.11')).toBe(200);
    expect(accounts.query('SELECT anonymous,user FROM anonymous_links').all()).toEqual([{ anonymous: anon, user: gone }]);
    expect(rows(anon).session).toBe(1);
    jar.clear();
    for (const [k, v] of admin) jar.set(k, v);
    expect(await call('/admin/remove-user', { userId: gone })).toBe(200);
    expect(accounts.query('SELECT count(*) AS n FROM "user" WHERE id IN (?,?)').get(gone, anon)).toEqual({ n: 0 });
    expect(rows(anon)).toEqual({ session: 0, account: 0, passkey: 0 });
    expect(accounts.query('SELECT count(*) AS n FROM anonymous_links').get()).toEqual({ n: 0 });
    expect(accounts.query('SELECT count(*) AS n FROM forgotten').get()).toEqual({ n: 0 });
    expect(atlas.query('SELECT count(*) AS n FROM actors WHERE user IN (?,?)').get(gone, anon)).toEqual({ n: 0 });
    expect([...media.keys()]).toEqual([`avatars/${stays}/a.webp`]);
    expect((await send('/get-session', undefined)).ok).toBe(true);
  });
  it('deletes no account linked to it as if anonymous', async () => {
    const { call, accounts, atlas, gone, stays } = await seeded('https://linked.glyphatlas.org');
    accounts.exec(`INSERT INTO anonymous_links(anonymous,user,at) VALUES('${stays}','${gone}','2026-10-20T00:00:00.000Z')`);
    expect(await call('/admin/remove-user', { userId: gone })).toBe(200);
    expect(accounts.query('SELECT count(*) AS n FROM "user" WHERE id=?').get(stays)).toEqual({ n: 1 });
    expect(accounts.query('SELECT count(*) AS n FROM session WHERE userId=?').get(stays)).toEqual({ n: 1 });
    expect(atlas.query('SELECT user,crop FROM favourites').all()).toEqual([{ user: stays, crop: 7 }]);
    expect(atlas.query('SELECT user FROM actors WHERE key=?').get(stays)).toEqual({ user: stays });
  });
  it('is finished by the hourly run, which waits while writes are paused', async () => {
    const { call, accounts, atlas, env, gone } = await seeded('https://hourly.glyphatlas.org');
    const batch = env.ATLAS.batch;
    (env.ATLAS as any).batch = async () => { throw new Error('D1_ERROR: unavailable') };
    expect(await call('/admin/remove-user', { userId: gone })).toBe(200);
    (env.ATLAS as any).batch = batch;
    const hourly = (more: Record<string, string>) => run({ ...env, ...more } as Env);
    await hourly({ WRITES_PAUSED: '1' });
    expect(accounts.query('SELECT user FROM forgotten').all()).toEqual([{ user: gone }]);
    expect(atlas.query('SELECT count(*) AS n FROM favourites WHERE user=?').get(gone)).toEqual({ n: 1 });
    await hourly({});
    expect(accounts.query('SELECT count(*) AS n FROM forgotten').get()).toEqual({ n: 0 });
    expect(atlas.query('SELECT count(*) AS n FROM favourites WHERE user=?').get(gone)).toEqual({ n: 0 });
    expect(atlas.query('SELECT count(*) AS n FROM actors WHERE user=?').get(gone)).toEqual({ n: 0 });
  });
  it('is finished by the next deletion when ATLAS fails between the two databases', async () => {
    const { call, accounts, atlas, env, gone, stays } = await seeded('https://retry.glyphatlas.org');
    const batch = env.ATLAS.batch;
    (env.ATLAS as any).batch = async () => { throw new Error('D1_ERROR: unavailable') };
    expect(await call('/admin/remove-user', { userId: gone })).toBe(200);
    expect(accounts.query('SELECT count(*) AS n FROM "user" WHERE id=?').get(gone)).toEqual({ n: 0 });
    expect(accounts.query('SELECT user FROM forgotten').all()).toEqual([{ user: gone }]);
    expect(atlas.query('SELECT count(*) AS n FROM favourites WHERE user=?').get(gone)).toEqual({ n: 1 });
    (env.ATLAS as any).batch = batch;
    expect(await call('/admin/remove-user', { userId: stays })).toBe(200);
    expect(accounts.query('SELECT count(*) AS n FROM forgotten').get()).toEqual({ n: 0 });
    expect(atlas.query('SELECT count(*) AS n FROM favourites').get()).toEqual({ n: 0 });
    expect(atlas.query('SELECT count(*) AS n FROM actors WHERE user IN (?,?)').get(gone, stays)).toEqual({ n: 0 });
  });
});
