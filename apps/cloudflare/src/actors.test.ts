import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { owned } from './auth';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
    db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  const at = '2026-10-01T00:00:00.000Z';
  db.exec(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES
      ('u1','Ada','ada@example.org',1,'${at}','${at}'),('u2','reviewer-00000002','b@anonymous.invalid',0,'${at}','${at}');
    INSERT INTO actors VALUES('u1','u1','account','${at}'),('reviewer-0000000a','u1','legacy','${at}'),('u2','u2','account','${at}');`);
  return { db };
}

describe('journal actors', () => {
  it('move with an anonymous user who signs in to an account', () => {
    const { db } = migrated();
    db.exec(`UPDATE actors SET user_id='u1' WHERE user_id='u2'`);
    expect(db.query(`SELECT actor FROM ${owned('u1')} ORDER BY 1`).all()).toEqual(
      [{ actor: 'reviewer-0000000a' }, { actor: 'u1' }, { actor: 'u2' }]);
    db.close();
  });
});

describe('claims on reviewer ids from before accounts', () => {
  it('rename a user named like an old id it does not hold, after granting any claim left waiting', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    const files = readdirSync(migrations).filter(f => f.endsWith('.sql')).sort();
    for (const file of files.filter(f => f < '0045')) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const at = '2026-10-01T00:00:00.000Z';
    db.exec(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES
        ('u1','reviewer-0000000a','a@x',0,'${at}','${at}'),('u2','reviewer-0000000b','b@x',0,'${at}','${at}'),('u3','Ada','c@x',1,'${at}','${at}');
      INSERT INTO actors VALUES('reviewer-0000000a','u1','legacy','${at}');
      INSERT INTO actor_claims VALUES('reviewer-0000000c','u3','${at}');`);
    for (const file of files.filter(f => f >= '0045')) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const names = db.query('SELECT id,name FROM "user" ORDER BY id').all() as { id: string; name: string }[];
    expect(names[0].name).toBe('reviewer-0000000a');
    expect(names[1].name).toMatch(/^anon-[0-9a-f]{6}$/);
    expect(names[2].name).toBe('Ada');
    expect(db.query("SELECT user_id FROM actors WHERE actor='reviewer-0000000c'").get()).toEqual({ user_id: 'u3' });
    db.close();
  });
});
