import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { FOLLOW_BATCH, followCorpus, formed, formsRoute, leastTypicalQuery } from './forms';

describe('form decisions outside cluster membership', () => {
  it('keeps the corrected identity without linking to a removed cluster', () => {
    const result = formed({ id: 'u', label: 'は', form_cluster: { id: 'old' } }, {
      id: 'u', cluster: 'old', clustered: 0, form: null, glyph_set: 1, cluster_form: null,
      issue: 'character', issue_character: '川', written_family: 'U+5DDD',
    }, { codePoints: () => 'U+5DDD' });
    expect(result.label).toBe('川');
    expect(result.grapheme).toBe('U+5DDD');
    expect(result.form_cluster).toBeUndefined();
    expect(result.form_decision.basis).toBe('form_glyph');
  });

  it('does not offer excluded glyphs among a surviving cluster’s outliers', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.exec(`INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,representatives)
      VALUES('c','U+306F','c',1,1,0,0,'[]');
      INSERT INTO form_units(id,family,cluster,rank,similarity,split,clustered) VALUES
        ('kept','U+306F','c',12,0.8,'2222222',1),('excluded','U+306F','c',13,0.7,'',0);`);
    expect(db.query(leastTypicalQuery()).all('U+306F')).toEqual([
      { cluster: 'c', id: 'kept', image: null, rank: 12 },
    ]);
    db.close();
  });

  it('gives a glyph named with a form the grapheme of that form', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    const files = readdirSync(migrations).filter(f => f.endsWith('.sql')).sort();
    for (const file of files.filter(f => f < '0030'))
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.exec(`INSERT INTO characters VALUES('U+4EFF','仿','','{"grapheme":{"code_point":"U+4EFF"}}','{}'),
        ('U+1B0A5','𛂥','','{"grapheme":{"code_point":"U+306F"}}','{}');
      INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note)
        VALUES('d1','2026-09-27','a','cluster','U+5023','仿','c','r','["k"]',''),
              ('d2','2026-09-27','a','cluster','U+306F','𛂥','h','r','["h"]','');
      INSERT INTO form_units(id,family,cluster,rank,similarity,split,clustered,cluster_form,form)
        VALUES('k','U+5023','c',0,1,'',1,'仿','仿'),('h','U+306F','h',0,1,'',1,'𛂥','𛂥'),('m','U+4E00','x',0,1,'',1,'𮧒','𮧒');
      INSERT INTO corpus_units(id,character,family,shuffle,object,offset,size) VALUES('k','仿','U+5023',1,'o',0,1);
      INSERT INTO form_bases(id,character,family) VALUES('k','倣','U+5023');`);
    for (const file of files.filter(f => f >= '0030'))
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    expect(db.query('SELECT id,written_family FROM form_decisions ORDER BY id').all()).toEqual([
      { id: 'd1', written_family: 'U+4EFF' }, { id: 'd2', written_family: 'U+306F' }]);
    // A form the character table lacks takes its own code point, as `tools.family` does.
    expect(db.query('SELECT id,written_family FROM form_units ORDER BY id').all()).toEqual([
      { id: 'h', written_family: 'U+306F' }, { id: 'k', written_family: 'U+4EFF' }, { id: 'm', written_family: 'U+2E9D2' }]);
    expect(db.query("SELECT character,family FROM corpus_units WHERE id='k'").get()).toEqual({ character: '仿', family: 'U+4EFF' });
    db.close();
  });
});

// D1's prepare/bind/first/run/batch over bun:sqlite, a batch in one transaction as D1 runs it.
function d1(db: Database) {
  const statement = (sql: string, args: unknown[] = []) => ({
    sql, args,
    bind: (...values: unknown[]) => statement(sql, values),
    first: async () => db.query(sql).get(...(args as any[])) ?? null,
    all: async () => ({ results: db.query(sql).all(...(args as any[])) }),
    run: async () => ({ meta: { changes: db.query(sql).run(...(args as any[])).changes } }),
  });
  return {
    prepare: (sql: string) => statement(sql),
    batch: async (list: ReturnType<typeof statement>[]) => db.transaction(() => list.map(s => /^\s*SELECT/i.test(s.sql)
      ? { results: db.query(s.sql).all(...(s.args as any[])), meta: { changes: 0 } }
      : { results: [], meta: { changes: db.query(s.sql).run(...(s.args as any[])).changes } }))(),
  };
}

describe('a decision’s corpus glyphs', () => {
  it('follow its form after the decision answers, in batches', async () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const count = FOLLOW_BATCH * 2 + 200;
    db.exec(`INSERT INTO form_families(code_point,char,label,count,cluster_count,forms,assigned,revision,rejected)
        VALUES('U+305F','た','た',${count},1,'[{"char":"𛁠"}]',0,'r',0);
      INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,representatives)
        VALUES('c','U+305F','c',${count},1,0,0,'[]');`);
    const insertUnit = db.prepare(`INSERT INTO form_units(id,family,cluster,rank,similarity,split) VALUES(?,'U+305F','c',?,1,'')`);
    const insertCorpus = db.prepare(`INSERT INTO corpus_units(id,character,family,shuffle,object,offset,size,production) VALUES(?,'た','U+305F',?,'o',0,1,?)`);
    for (let i = 0; i < count; i++) { insertUnit.run(`g${i}`, i); insertCorpus.run(`g${i}`, i, i % 2 ? 'printed' : 'unknown') }
    db.exec(`INSERT INTO corpus_characters SELECT character,production,count(*),0 FROM corpus_units GROUP BY 1,2`);
    const pending: Promise<unknown>[] = [];
    const env = { DB: d1(db) } as unknown as Env;
    const tools = {
      fail: (status: number, message: string): never => { throw new Error(`${status} ${message}`) },
      body: async (request: Request) => request.json() as Promise<Record<string, unknown>>,
      text: (value: unknown) => value as string, codePoints: (value: string) => value, family: async () => 'U+305F',
    };
    const request = new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST',
      body: JSON.stringify({ kind: 'cluster', cluster: 'c', form: '𛁠' }) });
    const decided = await formsRoute(env, request, '/atlas/forms/decisions', new URLSearchParams(), tools,
      { waitUntil: (p: Promise<unknown>) => pending.push(p) } as unknown as ExecutionContext, 'r') as Record<string, any>;
    expect(decided.count).toBe(count);
    expect(pending).toHaveLength(1);
    await Promise.all(pending);
    expect(db.query('SELECT count(*) AS n FROM corpus_follow').get()).toEqual({ n: 0 });
    expect(db.query('SELECT count(*) AS n FROM corpus_follow_drain').get()).toEqual({ n: 0 });
    expect(db.query('SELECT character,count(*) AS n FROM corpus_units GROUP BY 1').all()).toEqual([{ character: '𛁠', n: count }]);
    expect(db.query('SELECT character,production,n FROM corpus_characters ORDER BY production').all()).toEqual([
      { character: '𛁠', production: 'printed', n: count / 2 }, { character: '𛁠', production: 'unknown', n: count / 2 }]);
    expect(db.query("SELECT count(*) AS n FROM form_bases WHERE character='た'").get()).toEqual({ n: count });
    db.close();
  });

  it('are left to the drain that holds the list until its lease lapses', async () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort())
      db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.exec(`INSERT INTO corpus_follow VALUES('g');
      INSERT INTO corpus_follow_drain VALUES(1,${Date.now() + 60_000});`);
    const env = { DB: d1(db) } as unknown as Env;
    await followCorpus(env);
    expect(db.query('SELECT count(*) AS n FROM corpus_follow').get()).toEqual({ n: 1 });
    db.exec(`UPDATE corpus_follow_drain SET until=${Date.now() - 1}`);
    await followCorpus(env);
    expect(db.query('SELECT (SELECT count(*) FROM corpus_follow) AS listed,(SELECT count(*) FROM corpus_follow_drain) AS held').get())
      .toEqual({ listed: 0, held: 0 });
    db.close();
  });
});
