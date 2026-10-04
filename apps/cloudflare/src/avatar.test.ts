import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { setAvatar } from './avatar';

function setup() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  const at = '2026-10-02T00:00:00.000Z';
  db.exec(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES('00000000-0000-0000-0000-000000000001','Ada','Ada@Example.org ',1,'${at}','${at}');
    INSERT INTO account(id,accountId,providerId,userId,createdAt,updatedAt) VALUES('a1','583231','github','00000000-0000-0000-0000-000000000001','${at}','${at}');`);
  const stored = new Map<string, Uint8Array>();
  const MEDIA = { put: async (key: string, bytes: Uint8Array) => { stored.set(key, bytes) }, delete: async (key: string) => { stored.delete(key) } };
  const env = { DB: d1(db), MEDIA } as unknown as Env;
  const me = { id: '00000000-0000-0000-0000-000000000001', name: 'Ada', image: null, anonymous: false, admin: false };
  const image = () => (db.query('SELECT image FROM "user"').get() as { image: string | null }).image;
  return { db, env, me, stored, image };
}
const webp = (extra = 0) => new Uint8Array([...new TextEncoder().encode('RIFF'), 0, 0, 0, 0, ...new TextEncoder().encode('WEBPVP8 '), ...new Array(16 + extra).fill(1)]);
const post = (body?: BodyInit, type = 'application/json') => new Request('https://glyphatlas.org/api/account/avatar', { method: 'POST', headers: { 'content-type': type }, body });
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch });

describe('a reader\'s picture', () => {
  it('comes from GitHub, the initial, or an upload that replaces the last one', async () => {
    const { env, me, stored, image } = setup();
    expect((await setAvatar(env, me, 'github', post('{}'))).status).toBe(200);
    expect(image()).toBe('https://avatars.githubusercontent.com/u/583231?v=4');
    expect((await setAvatar(env, me, 'upload', post(webp(), 'image/webp'))).status).toBe(200);
    const first = image()!;
    expect(first).toMatch(/^\/api\/avatars\/00000000-0000-0000-0000-000000000001\/[0-9a-f]{16}\.webp$/);
    expect((await setAvatar(env, me, 'upload', post(webp(1), 'image/webp'))).status).toBe(200);
    expect(stored.size).toBe(1);
    expect((await setAvatar(env, me, 'initial', post('{}'))).status).toBe(200);
    expect(image()).toBeNull();
    expect(stored.size).toBe(0);
  });
  it('comes from a connected Google account, when it has a picture', async () => {
    const { db, env, me, image } = setup();
    expect((await setAvatar(env, me, 'google', post('{}'))).status).toBe(404);
    const at = '2026-10-02T00:00:00.000Z';
    db.exec(`INSERT INTO account(id,accountId,providerId,userId,createdAt,updatedAt) VALUES('a2','1234','google','00000000-0000-0000-0000-000000000001','${at}','${at}');
      INSERT INTO account_profiles(account,provider,image,at) VALUES('a2','google','https://lh3.googleusercontent.com/a/x=s256-c','${at}');`);
    expect((await setAvatar(env, me, 'google', post('{}'))).status).toBe(200);
    expect(image()).toBe('https://lh3.googleusercontent.com/a/x=s256-c');
  });
  it('refuses what is not a small WebP, and an anonymous user', async () => {
    const { env, me } = setup();
    expect((await setAvatar(env, me, 'upload', post(new Uint8Array([137, 80, 78, 71, 0, 0, 0, 0, 0, 0, 0, 0, 0]), 'image/webp'))).status).toBe(415);
    expect((await setAvatar(env, me, 'upload', post(webp(), 'image/png'))).status).toBe(415);
    expect((await setAvatar(env, me, 'upload', post(webp(300 * 1024), 'image/webp'))).status).toBe(413);
    expect((await setAvatar(env, { ...me, anonymous: true }, 'initial', post('{}'))).status).toBe(403);
    expect((await setAvatar(env, me, 'elsewhere', post('{}'))).status).toBe(422);
  });
  it('takes the Gravatar of the address, trimmed and lower-cased, only when there is one', async () => {
    const { env, me, image } = setup();
    let asked = '';
    globalThis.fetch = (async (url: string) => { asked = url; return new Response(null, { status: 404 }) }) as typeof fetch;
    expect((await setAvatar(env, me, 'gravatar', post('{}'))).status).toBe(404);
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('ada@example.org')))].map(b => b.toString(16).padStart(2, '0')).join('');
    expect(asked).toBe(`https://gravatar.com/avatar/${hash}?s=256&d=404`);
    globalThis.fetch = (async () => new Response(null, { status: 200 })) as unknown as typeof fetch;
    expect((await setAvatar(env, me, 'gravatar', post('{}'))).status).toBe(200);
    expect(image()).toBe(`https://gravatar.com/avatar/${hash}?s=256`);
  });
});
