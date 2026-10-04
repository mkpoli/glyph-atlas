import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { connections, recordProfile } from './connections';

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch });
const token = (claims: object) => `h.${btoa(JSON.stringify(claims)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')}.s`;

describe('connected accounts', () => {
  it('say who each account is, from an ID token or the provider, and keep it', async () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const at = '2026-10-03T00:00:00.000Z';
    db.prepare(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES('u1','まくぽり','m@x',1,?,?)`).run(at, at);
    db.prepare(`INSERT INTO account(id,accountId,providerId,userId,accessToken,idToken,createdAt,updatedAt) VALUES
      ('a1','583231','github','u1','expired',NULL,?,?),('a2','1234','google','u1',NULL,?,?,?)`).run(at, at, token({ email: 'm@gmail.example', name: 'M', picture: 'https://p/1' }), at, at);
    const asked: string[] = [];
    globalThis.fetch = (async (url: string) => {
      asked.push(url);
      if (url === 'https://api.github.com/user') return new Response('', { status: 401 });
      return Response.json({ login: 'octocat', name: 'The Octocat', email: null, avatar_url: 'https://a/1' });
    }) as typeof fetch;
    const env = { DB: d1(db) } as unknown as Env;
    const { items } = await connections(env, 'u1');
    expect(items.map(i => [i.provider, i.handle, i.name, i.email])).toEqual([['github', 'octocat', 'The Octocat', null], ['google', null, 'M', 'm@gmail.example']]);
    expect(asked).toEqual(['https://api.github.com/user', 'https://api.github.com/user/583231']);
    asked.length = 0;
    await connections(env, 'u1');
    expect(asked).toEqual([]);
    db.close();
  });
  it('keep a user on their Google picture when they change it at Google', async () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const at = '2026-10-03T00:00:00.000Z';
    const old = 'https://lh3.googleusercontent.com/a/old=s256-c';
    db.prepare(`INSERT INTO "user"(id,name,email,emailVerified,image,createdAt,updatedAt) VALUES('u1','M','m@x',1,?,?,?)`).run(old, at, at);
    db.prepare(`INSERT INTO account(id,accountId,providerId,userId,idToken,createdAt,updatedAt) VALUES('a2','1234','google','u1',?,?,?)`)
      .run(token({ picture: 'https://lh3.googleusercontent.com/a/new=s96-c' }), at, at);
    db.prepare(`INSERT INTO account_profiles(account,provider,image,at) VALUES('a2','google',?,?)`).run(old, at);
    await recordProfile({ DB: d1(db) } as unknown as Env, 'a2');
    expect((db.query('SELECT image FROM "user"').get() as { image: string }).image).toBe('https://lh3.googleusercontent.com/a/new=s256-c');
    db.close();
  });
});
