import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { IN_LIST, MAX_IDS, actorPeople, people } from './accounts';
import { claim } from './auth';

function migrated(dir: string) {
  const db = new Database(':memory:');
  const migrations = new URL(`../${dir}/`, import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
// D1 binds at most 100 values to a statement; each statement's count is kept.
function bounded(db: Database, bound: number[]) {
  const base = d1(db);
  return { ...base, prepare: (sql: string) => {
    const statement = base.prepare(sql), bind = statement.bind;
    return { ...statement, bind: (...values: unknown[]) => {
      if (values.length > 100) throw new Error('D1_ERROR: too many SQL variables');
      bound.push(values.length);
      return bind(...values);
    } };
  } };
}
function setup() {
  const accounts = migrated('migrations-accounts'), atlas = migrated('migrations-atlas');
  const at = '2026-10-20T00:00:00.000Z';
  const user = accounts.prepare(`INSERT INTO "user"(id,name,email,emailVerified,image,createdAt,updatedAt,isAnonymous) VALUES(?,?,?,1,?,?,?,?)`);
  for (let i = 0; i < 250; i++) user.run(`u${i}`, `name ${i}`, `${i}@x`, i % 2 ? `/p/${i}` : null, at, at, i === 4 ? 1 : null);
  const actor = atlas.prepare(`INSERT INTO actors(id,key,user,via,at) VALUES(?,?,?,?,1)`);
  for (let i = 0; i < 250; i++) actor.run(i + 1, `k${i}`, i < 240 ? `u${i}` : i < 245 ? `deleted${i}` : null, i < 245 ? 'account' : null);
  const bound: number[] = [];
  const env = { ACCOUNTS: bounded(accounts, bound), ATLAS: bounded(atlas, bound) } as unknown as Env;
  return { env, accounts, atlas, bound };
}

describe('names for rows written in ATLAS', () => {
  it('come from ACCOUNTS in statements of at most a hundred ids, deleted users left out', async () => {
    const { env, bound } = setup();
    const found = await people(env, [...Array.from({ length: 250 }, (_, i) => `u${i}`), 'u3', null, 'nobody']);
    expect(found.size).toBe(250);
    expect(found.get('u3')).toEqual({ name: 'name 3', image: '/p/3', anonymous: false });
    expect(found.get('u4')?.anonymous).toBe(true);
    expect(found.has('nobody')).toBe(false);
    expect(bound).toEqual([IN_LIST, IN_LIST, 51]);
  });
  it('name each actor by its user, and leave an actor with no account unnamed', async () => {
    const { env, bound } = setup();
    const found = await actorPeople(env, Array.from({ length: 250 }, (_, i) => i + 1));
    expect(found.get(1)).toEqual({ key: 'k0', user: 'u0', name: 'name 0', image: null, anonymous: false });
    expect(found.get(5)).toEqual({ key: 'k4', user: 'u4', name: 'name 4', image: null, anonymous: true });
    // A user deleted in ACCOUNTS whose actor links are not yet removed.
    expect(found.get(242)).toEqual({ key: 'k241', user: null, name: null, image: null, anonymous: false });
    expect(found.get(250)).toEqual({ key: 'k249', user: null, name: null, image: null, anonymous: false });
    expect(bound).toEqual([100, 100, 50, 100, 100, 45]);
    expect(await actorPeople(env, [])).toEqual(new Map());
  });
  it('refuse more ids than one request may look up', async () => {
    const { env } = setup();
    await expect(people(env, Array.from({ length: MAX_IDS + 1 }, (_, i) => `u${i}`))).rejects.toThrow(RangeError);
  });
});

describe('claims on reviewer ids from before accounts', () => {
  const anon = { id: 'u2', name: 'anon-000002', image: null, anonymous: true, admin: false };
  // Ids that wrote before accounts: an actor row with neither user nor `via`.
  const wrote = (atlas: any, ...ids: string[]) => { for (const id of ids) atlas.prepare(`INSERT INTO actors(key,user,via,at) VALUES(?,NULL,NULL,NULL)`).run(id) };
  const limited = (env: Env, allowed = () => true) => ({ ...env, OLD_IDS: { limit: async () => ({ success: allowed() }) } }) as unknown as Env;
  it('hold at once for the first account, and stop at an id another account holds', async () => {
    const { env: base, atlas } = setup(), env = limited(base);
    atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-0000000a','u1','legacy',1)`);
    wrote(atlas, 'reviewer-0000000b', 'reviewer-0000000d');
    expect((await claim(env, anon, 'someone', null)).status).toBe(422);
    expect((await claim(env, anon, 'reviewer-0000000b', null)).status).toBe(200);
    expect((await claim(env, anon, 'reviewer-0000000b', null)).status).toBe(200);
    expect(atlas.query("SELECT user,via,typeof(at) AS at FROM actors WHERE key='reviewer-0000000b'").get()).toEqual({ user: 'u2', via: 'legacy', at: 'integer' });
    expect((await claim(env, anon, 'reviewer-0000000a', null)).status).toBe(409);
    expect((await claim(env, anon, 'reviewer-0000000d', null)).status).toBe(200);
    // An id an account held keeps no user once that account is deleted, and nobody else takes it.
    atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-0000000f',NULL,'legacy',1)`);
    expect((await claim(env, anon, 'reviewer-0000000f', null)).status).toBe(409);
    expect(atlas.query("SELECT user,via FROM actors WHERE key='reviewer-0000000f'").get()).toEqual({ user: null, via: 'legacy' });
  });
  it('take no id that never wrote, and write no row for it', async () => {
    const { env: base, atlas } = setup(), env = limited(base);
    expect((await claim(env, anon, 'reviewer-00000077', null)).status).toBe(422);
    expect(atlas.query("SELECT count(*) AS n FROM actors WHERE key='reviewer-00000077'").get()).toEqual({ n: 0 });
  });
  it('stop an address that sends too many ids, before reading any', async () => {
    const { env: base, atlas, bound } = setup();
    const keys: string[] = [];
    const env = { ...base, OLD_IDS: { limit: async ({ key }: { key: string }) => { keys.push(key); return { success: false } } } } as unknown as Env;
    wrote(atlas, 'reviewer-0000000b');
    bound.length = 0;
    expect((await claim(env, anon, 'reviewer-0000000b', '203.0.113.9')).status).toBe(429);
    expect(keys).toEqual(['203.0.113.9']);
    expect(atlas.query("SELECT user FROM actors WHERE key='reviewer-0000000b'").get()).toEqual({ user: null });
  });
  it('stop at five, also when two are sent at once', async () => {
    const { env: base, atlas } = setup(), env = limited(base);
    atlas.exec(`INSERT INTO actors(key,user,via,at) VALUES('reviewer-00000001','u2','legacy',1),('reviewer-00000002','u2','legacy',1),
      ('reviewer-00000003','u2','legacy',1),('reviewer-00000004','u2','legacy',1)`);
    wrote(atlas, 'reviewer-0000000a', 'reviewer-0000000b');
    const statuses = (await Promise.all(['a', 'b'].map(id => claim(env, anon, `reviewer-0000000${id}`, null)))).map(r => r.status);
    expect(statuses.sort()).toEqual([200, 409]);
    expect(atlas.query("SELECT count(*) AS n FROM actors WHERE user='u2' AND via='legacy'").get()).toEqual({ n: 5 });
    // A claim of an id the account already holds still answers that it holds it.
    expect((await claim(env, anon, 'reviewer-00000001', null)).status).toBe(200);
  });
});
