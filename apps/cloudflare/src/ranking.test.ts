import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { rankingQuery, rankingRow } from './ranking';

describe('the reviewer ranking', () => {
  it('counts every standing decision per person, merging the ids a user holds', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    // The rows stand alone here, without the crops they apply to.
    db.exec('DROP TRIGGER event_revision_guard; DROP TRIGGER event_apply; DROP TRIGGER written_form_revision_guard; DROP TRIGGER written_form_apply;');
    const at = '2026-10-02T00:00:00.000Z';
    db.exec(`INSERT INTO "user"(id,name,email,emailVerified,image,createdAt,updatedAt,isAnonymous) VALUES
        ('u1','まくぽり','m@x',1,'https://x/a.png','${at}','${at}',0),('u2','anon-00aa11','b@anonymous.invalid',0,NULL,'${at}','${at}',1);
      INSERT INTO actors VALUES('u1','u1','account','${at}'),('reviewer-0000000a','u1','legacy','${at}'),('u2','u2','account','${at}');
      INSERT INTO submissions(id,actor,request,response,at,undone) VALUES
        ('u1:s','u1','{}','{}','${at}',0),('reviewer-0000000a:s','reviewer-0000000a','{}','{}','${at}',0),
        ('u2:s','u2','{}','{}','${at}',0),('u2:gone','u2','{}','{}','${at}',1),('reviewer-0000000f:s','reviewer-0000000f','{}','{}','${at}',0);`);
    const event = db.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES(?,?,'t',?,0,'{}','{}','{}','{}',?,'${at}')`);
    let n = 0;
    const add = (submission: string, actor: string, count: number, kind = 'review') => { for (let i = 0; i < count; i++) event.run(`e${n++}`, submission, actor, kind) };
    add('u1:s', 'u1', 2); add('reviewer-0000000a:s', 'reviewer-0000000a', 3); add('u2:s', 'u2', 1); add('u2:gone', 'u2', 5);
    add('u2:gone', 'u1', 1, 'undo'); add('reviewer-0000000f:s', 'reviewer-0000000f', 4);
    db.exec(`INSERT INTO written_forms(id,submission,target,actor,revision,pixels,label,form,request,at) VALUES('w1','u2:w','t','u2',1,'p','あ','あ','{}','${at}');
      INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note) VALUES('d1','${at}','u1','cluster','U+3042','𛀂','c','r','[]','');`);
    const rows = db.query(rankingQuery()).all().map((row, i) => rankingRow(row as Record<string, unknown>, i + 1));
    expect(rows).toEqual([
      { place: 1, total: 6, user: 'u1', anonymous: false, name: 'まくぽり', image: 'https://x/a.png' },
      { place: 2, total: 4, user: null, anonymous: true, name: 'reviewer-0000000f', image: null },
      { place: 3, total: 2, user: 'u2', anonymous: true, name: 'anon-00aa11', image: null },
    ]);
    db.close();
  });
});
