import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { canonical, history, historyQuery } from './index';
import { summary } from './admin';
import { claim, owned } from './auth';
import { d1 } from './forms.test';

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
    .run(id, actor + ':s', 'unit', actor, `2026-10-01T00:${String(minute).padStart(2, '0')}:00.000Z`);
  return { db, event };
}

describe('journal actors', () => {
  it('give a user the rows of every id they hold, and name each row by its holder', async () => {
    const { db, event } = migrated();
    event('e1', 'reviewer-0000000a', 1); event('e2', 'u1', 2); event('e3', 'u2', 3); event('e4', 'reviewer-0000000f', 4);
    const mine = await history({ DB: d1(db) } as unknown as Env, new URLSearchParams('user=u1'), null);
    expect(mine.items.map((i: any) => [i.id, i.reviewer.user, i.reviewer.name])).toEqual([['e2', 'u1', 'Ada'], ['e1', 'u1', 'Ada']]);
    const all = historyQuery(null, null, null);
    expect(db.query(all.sql).all(...all.values, 10).map((r: any) => [r.id, r.user, r.name])).toEqual([
      ['e4', null, null], ['e3', 'u2', 'reviewer-00000002'], ['e2', 'u1', 'Ada'], ['e1', 'u1', 'Ada']]);
    db.close();
  });
  it('page a user holding several ids newest first across them, the cursor carrying on where the page stopped', async () => {
    const { db, event } = migrated();
    const env = { DB: d1(db) } as unknown as Env;
    // Interleaved, with two rows at the same instant that only the id orders.
    event('a1', 'reviewer-0000000a', 1); event('b1', 'u1', 2); event('a2', 'reviewer-0000000a', 3); event('x1', 'u2', 4);
    event('b2', 'u1', 5); event('b3', 'u1', 6); event('a3', 'reviewer-0000000a', 6); event('a4', 'reviewer-0000000a', 7);
    const pages: string[][] = [];
    let before: string | null = null;
    do {
      const page: any = await history(env, new URLSearchParams({ user: 'u1', limit: '3', ...(before ? { before } : {}) }), 'u1');
      pages.push(page.items.map((i: any) => i.id));
      expect(page.items.every((i: any) => i.reviewer.mine)).toBe(true);
      before = page.next;
    } while (before);
    expect(pages).toEqual([['a4', 'b3', 'a3'], ['b2', 'a2', 'b1'], ['a1']]);
    db.close();
  });
  it('read a user holding more ids than one statement takes in groups, merged into one order', async () => {
    const { db, event } = migrated();
    const env = { DB: d1(db) } as unknown as Env;
    // The Worker reads two ids to a statement (index.ts HISTORY_IDS); a Worker module exports only handlers.
    const held = Array.from({ length: 7 }, (_, i) => `reviewer-${String(i).padStart(8, '0')}`);
    db.exec(`INSERT INTO actors VALUES ${held.map(a => `('${a}','u1','legacy','2026-10-01T00:00:00.000Z')`).join(',')}`);
    // Ids in later groups wrote the newest rows, and two rows tie across groups.
    const minute = [3, 10, 20, 7, 30, 20, 1];
    held.forEach((actor, i) => event(`h${i}`, actor, minute[i]));
    const want = held.map((_, i) => i).sort((a, b) => minute[b] - minute[a] || b - a).map(i => `h${i}`);
    expect(want).toEqual(['h4', 'h5', 'h2', 'h1', 'h3', 'h0', 'h6']);
    const pages: string[][] = [];
    let before: string | null = null;
    do {
      const page: any = await history(env, new URLSearchParams({ user: 'u1', limit: '3', ...(before ? { before } : {}) }), null);
      pages.push(page.items.map((i: any) => i.id));
      before = page.next;
    } while (before);
    expect(pages.flat()).toEqual(want);
    db.close();
  });
  it('serve a user\'s page from event_actor_history without sorting what the ids ever wrote', () => {
    const { db } = migrated();
    for (const cursor of [null, { at: '2026-10-01T00:00:00.000Z', id: 'e' }]) {
      const { sql, values } = historyQuery(['u1', 'reviewer-0000000a', 'u2'], null, cursor);
      const plan = (db.query('EXPLAIN QUERY PLAN ' + sql).all(...values, 41) as { detail: string }[]).map(r => r.detail);
      expect(plan.filter(d => d.startsWith('SEARCH events')).every(d => d.includes('USING INDEX event_actor_history'))).toBe(true);
      expect(plan.filter(d => d.startsWith('SEARCH events')).length).toBe(3);
      expect(plan.some(d => d.includes('TEMP B-TREE'))).toBe(false);
    }
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
  it('hold at once for the first account, and stop at an id another account holds', async () => {
    const { db } = migrated();
    const env = { DB: d1(db) } as unknown as Env;
    const anon = { id: 'u2', name: 'anon-000002', image: null, anonymous: true, admin: false };
    expect((await claim(env, anon, 'someone')).status).toBe(422);
    expect((await claim(env, anon, 'reviewer-0000000b')).status).toBe(200);
    expect((await claim(env, anon, 'reviewer-0000000b')).status).toBe(200);
    expect(db.query("SELECT user_id FROM actors WHERE actor='reviewer-0000000b'").get()).toEqual({ user_id: 'u2' });
    expect((await claim(env, anon, 'reviewer-0000000a')).status).toBe(409);
    for (const id of ['c', 'd', 'e', 'f']) expect((await claim(env, anon, `reviewer-0000000${id}`)).status).toBe(200);
    expect((await claim(env, anon, 'reviewer-00000010')).status).toBe(429);
    db.close();
  });
});

describe('admin summaries', () => {
  it('say what each kind of submission asked for', () => {
    expect(summary(canonical({ target: null, input: { label: 'あ', answers: [{ verdict: 'match' }, { verdict: 'wrong' }, { verdict: 'match' }], skipped: [{}] } })))
      .toEqual({ kind: 'round', label: 'あ', verdicts: { match: 2, wrong: 1 }, skipped: 1 });
    expect(summary(canonical({ target: '@batch', input: { character: 'い', crops: [{}, {}] } })))
      .toEqual({ kind: 'correction', label: 'い', verdicts: { wrong: 2 } });
    expect(summary(canonical({ target: 'u1', input: { verdict: 'wrong', issue: 'character', character: 'う' } })))
      .toEqual({ kind: 'crop', target: 'u1', label: 'う', verdicts: { wrong: 1 } });
  });
});

describe('passed rounds in the history', () => {
  it('list a standing round once, with the crops it passed, and drop an undone one', () => {
    const { db, event } = migrated();
    const at = '2026-10-01T00:05:00.000Z';
    db.exec(`INSERT INTO submissions(id,actor,request,response,at,undone) VALUES
        ('u1:r1','u1','{"input":{"label":"あ"},"target":null}','{}','${at}',0),('u1:r2','u1','{"input":{"label":"い"},"target":null}','{}','${at}',1);
      PRAGMA foreign_keys=OFF;
      INSERT INTO seen(target,submission,box,image_sha256,at) VALUES('a','u1:r1',NULL,'h','${at}'),('b','u1:r1',NULL,'h','${at}'),('c','u1:r2',NULL,'h','${at}');`);
    event('e1', 'u1', 1);
    const { sql, values } = historyQuery(['u1', 'reviewer-0000000a'], null, null);
    const rows = db.query(sql).all(...values, 10) as Record<string, unknown>[];
    expect(rows.map(r => [r.id, r.kind, r.label, r.event])).toEqual([['u1:r1', 'passed', 'あ', 2], ['e1', 'review', null, '{"evidence":"{}"}']]);
    const label = historyQuery(null, 'あ', null);
    expect((db.query(label.sql).all(...label.values, 10) as Record<string, unknown>[]).map(r => r.id)).toEqual(['u1:r1']);
    db.close();
  });
});

