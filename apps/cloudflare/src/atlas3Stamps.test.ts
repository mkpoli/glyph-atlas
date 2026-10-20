import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { d1 } from './forms.test';
import { catalogueKey, catalogueStamps, catalogueVersion, publicationKey, stampsQuery } from './atlas3Stamps';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function setup() {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL('../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  db.exec(`INSERT INTO generation VALUES(0,1); INSERT INTO imports VALUES('build:1',1)`);
  const reads: string[] = [];
  const atlas = d1(db);
  const ATLAS = { prepare: (sql: string) => { reads.push(sql); return atlas.prepare(sql); } };
  return { db, reads, env: (lag: number) => ({ ATLAS, CATALOGUE_LAG: String(lag) }) as unknown as Env };
}

describe('the catalogue version', () => {
  it('moves with every write a listing shows', async () => {
    const { db, env } = setup();
    const keys = [await catalogueVersion(env(0))];
    for (const write of [
      `INSERT INTO imports VALUES('publish:2',2)`,
      `UPDATE generation SET gen=2 WHERE id=0`,
      `INSERT INTO submissions(id,key,actor,request,response,at) VALUES(1,'a:1',1,'{}','{}',1)`,
      `INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,16777216,1,0,2,1,1,1,0)`,
      `UPDATE submissions SET undone=1 WHERE id=1`,
      // An undo that takes its event back still moves the version, by the undone count.
      `DELETE FROM events WHERE id=1; INSERT INTO submissions(id,key,actor,request,response,at,undone) VALUES(2,'a:2',1,'{}','{}',2,1)`,
      `INSERT INTO ledger_submissions VALUES('l1','a','{}','{}','t')`,
      `INSERT INTO form_decisions(seq,id,at,actor,kind,family,revision,units,crops,note) VALUES(1,'d1','t','a','k','f','r','[]','[]','')`,
    ]) {
      db.exec(write);
      keys.push(await catalogueVersion(env(0)));
    }
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys[0]).toBe('1:1:0:0:0:0:0');
  });

  it('keys the publication by generation and imports alone', async () => {
    const { db, env } = setup();
    const before = publicationKey(await catalogueStamps(env(0)));
    db.exec(`INSERT INTO submissions(id,key,actor,request,response,at) VALUES(1,'a:1',1,'{}','{}',1)`);
    expect(publicationKey(await catalogueStamps(env(0)))).toBe(before);
    db.exec(`UPDATE generation SET gen=2 WHERE id=0`);
    expect(publicationKey(await catalogueStamps(env(0)))).not.toBe(before);
    expect(catalogueKey(null)).toBe('0:0:0:0:0:0:0');
  });

  it('is held for the lag, and read afresh for a browser that just wrote', async () => {
    const { db, reads, env } = setup();
    // The isolate keeps one copy for every test, so this one reads later than any other.
    const at = Date.now() + 10_000_000;
    const first = await catalogueVersion(env(60), at);
    db.exec(`INSERT INTO imports VALUES('publish:2',2)`);
    expect(await catalogueVersion(env(60), at + 30_000)).toBe(first);
    expect(reads).toHaveLength(1);
    expect(await catalogueVersion(env(0), at + 31_000)).not.toBe(first);
    expect(await catalogueVersion(env(60), at + 100_000)).not.toBe(first);
  });

  it('reads each stamp by an index seek, the undone count from undone rows only', () => {
    const { db } = setup();
    const plan = (db.query(`EXPLAIN QUERY PLAN ${stampsQuery()}`).all() as { detail: string }[]).map(row => row.detail);
    expect(plan.filter(detail => detail.startsWith('SCAN')).sort()).toEqual(['SCAN CONSTANT ROW', 'SCAN submissions USING COVERING INDEX submission_undone']);
  });
});
