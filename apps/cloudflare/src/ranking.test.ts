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
    db.exec('DROP TRIGGER event_revision_guard; DROP TRIGGER event_apply;');
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
    // The ledger: u2 chooses a form (its naming claim beside it counts as nobody's), then chooses
    // another, which retracts the first; chooses a member of the grapheme, which renamed the crop by a
    // review the claim names; claims an alternative set of two; accepts u1's claim, twice. u1's own claim and a
    // retraction count once and not at all; a source's claims (attested or derived) and a model's, and
    // a claim moved from the written forms, count as nobody's.
    db.exec(`INSERT INTO submissions(id,actor,request,response,at,undone) VALUES('u2:rename','u2','{}','{}','${at}',0);
      INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES
        ('u2:f1','u2','{"crop":"t","input":{"form":"⿺辶𦊷"}}','{}','${at}'),('u2:f2','u2','{"crop":"t","input":{"form":"𮟃"}}','{}','${at}'),
        ('u2:f3','u2','{"crop":"t2","input":{"form":"𛀂","review":"rename"}}','{}','${at}'),('u2:set','u2','{}','{}','${at}'),
        ('u2:ok','u2','{}','{}','${at}'),('u1:c','u1','{}','{}','${at}');`);
    const claim = db.prepare(`INSERT INTO assertions(id,submission,subject,predicate,object,value,alternative_set,tier,asserted_by,asserted_at,run,legacy)
      VALUES(?,?,?,?,?,?,?,?,?,'${at}',?,?)`);
    claim.run('a1', 'u2:f1', 't', 'has_form', 'fm:1', null, null, 'observed', 'u2', null, null);
    claim.run('a1n', 'u2:f1', 'fm:1', 'represented_by', 'rp:1', null, null, 'observed', 'u2', null, null);
    claim.run('a2', 'u2:f2', 't', 'has_form', 'fm:2', null, null, 'observed', 'u2', null, null);
    claim.run('a3', 'u2:f3', 't2', 'has_form', 'fm:3', null, null, 'observed', 'u2', null, null);
    claim.run('a4', 'u2:set', 't3', 'has_form', 'fm:1', null, 's', 'observed', 'u2', null, null);
    claim.run('a5', 'u2:set', 't3', 'has_form', 'fm:2', null, 's', 'observed', 'u2', null, null);
    claim.run('a6', 'u1:c', 't4', 'has_form', null, '"unreadable"', null, 'observed', 'u1', null, null);
    claim.run('a7', null, 'doc', 'date_written', null, '{}', null, 'attested', 'source:x', null, null);
    claim.run('a7e', null, 'doc', 'date_written', null, '{"of":"x"}', null, 'derived', 'source:x', null, null);
    claim.run('a8', null, 't5', 'has_form', 'fm:1', null, null, 'derived', 'model', 'run-1', null);
    claim.run('a9', 'u2:w', 't6', 'has_form', 'fm:1', null, null, 'observed', 'u2', null, 'written_forms:w1');
    db.exec(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at) VALUES
      ('x1','u2:f2','a1','retract','u2','${at}'),('x2','u2:ok','a6','accept','u2','${at}'),('x3','u2:ok2','a6','accept','u2','${at}');`);
    const rows = db.query(rankingQuery()).all().map((row, i) => rankingRow(row as Record<string, unknown>, i + 1));
    expect(rows).toEqual([
      { place: 1, total: 7, user: 'u1', anonymous: false, name: 'まくぽり', image: 'https://x/a.png' },
      { place: 2, total: 5, user: 'u2', anonymous: true, name: 'anon-00aa11', image: null },
      { place: 3, total: 4, user: null, anonymous: true, name: 'reviewer-0000000f', image: null },
    ]);
    // The whole journal is read once a reading, each table by one pass and every lookup by its key.
    const plan = db.query('EXPLAIN QUERY PLAN ' + rankingQuery()).all().map(row => (row as { detail: string }).detail);
    for (const lookup of ['x', 's', 'l']) expect(plan.some(d => d.startsWith(`SCAN ${lookup} `) || d === `SCAN ${lookup}`)).toBe(false);
    expect(plan.filter(d => /^SCAN a\b/.test(d))).toEqual(['SCAN a USING INDEX assertion_actor']);
    db.close();
  });
});
