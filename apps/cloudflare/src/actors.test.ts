import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { historyQuery } from './index';
import { owned } from './auth';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
    db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  // The rows stand alone here, without the crops a review applies to.
  db.exec('DROP TRIGGER event_revision_guard; DROP TRIGGER event_apply;');
  const at = '2026-10-01T00:00:00.000Z';
  db.exec(`INSERT INTO "user"(id,name,email,emailVerified,createdAt,updatedAt) VALUES
      ('u1','Ada','ada@example.org',1,'${at}','${at}'),('u2','reviewer-00000002','b@anonymous.invalid',0,'${at}','${at}');
    INSERT INTO actors VALUES('u1','u1','account','${at}'),('reviewer-0000000a','u1','legacy','${at}'),('u2','u2','account','${at}');`);
  const event = (id: string, actor: string, minute: number) => db.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,
    before_data,after_data,event,snapshot,kind,at) VALUES(?,?,?,?,0,'{}','{}','{"evidence":"{}"}','{}','review',?)`)
    .run(id, actor + ':s', 'unit', actor, `2026-10-01T00:0${minute}:00.000Z`);
  return { db, event };
}

describe('journal actors', () => {
  it('give a user the rows of every id they hold, and name each row by its holder', () => {
    const { db, event } = migrated();
    event('e1', 'reviewer-0000000a', 1); event('e2', 'u1', 2); event('e3', 'u2', 3); event('e4', 'reviewer-0000000f', 4);
    const mine = historyQuery('u1', null, null);
    expect(db.query(mine.sql).all(...mine.values, 10).map((r: any) => r.id)).toEqual(['e2', 'e1']);
    const all = historyQuery(null, null, null);
    expect(db.query(all.sql).all(...all.values, 10).map((r: any) => [r.id, r.user, r.name])).toEqual([
      ['e4', null, null], ['e3', 'u2', 'reviewer-00000002'], ['e2', 'u1', 'Ada'], ['e1', 'u1', 'Ada']]);
    db.close();
  });
  it('move with an anonymous user who signs in to an account', () => {
    const { db } = migrated();
    db.exec(`UPDATE actors SET user_id='u1' WHERE user_id='u2'`);
    expect(db.query(`SELECT actor FROM ${owned('u1')} ORDER BY 1`).all()).toEqual(
      [{ actor: 'reviewer-0000000a' }, { actor: 'u1' }, { actor: 'u2' }]);
    db.close();
  });
});
