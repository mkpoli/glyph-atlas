import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { FOLLOW_BATCH, describedClaimsQuery, followCorpus, formed, formsRoute, leastTypicalQuery, member, membersQuery } from './forms';

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
export function d1(db: Database) {
  const statement = (sql: string, args: unknown[] = []) => ({
    sql, args,
    bind: (...values: unknown[]) => statement(sql, values),
    first: async () => db.query(sql).get(...(args as any[])) ?? null,
    all: async () => ({ results: db.query(sql).all(...(args as any[])) }),
    run: async () => ({ meta: { changes: db.query(sql).run(...(args as any[])).changes } }),
  });
  return {
    // D1 refuses a compound SELECT of more than five terms (`too many terms in compound SELECT`).
    prepare: (sql: string) => {
      if ((sql.match(/\bUNION\b/gi) ?? []).length > 4) throw new Error('D1_ERROR: too many terms in compound SELECT: SQLITE_ERROR');
      return statement(sql);
    },
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

describe('a cluster\'s members', () => {
  it('carry what is painted in a local crop\'s place until it loads, and nothing for a glyph with no row', () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
      VALUES('a','local','あ','handwritten','kana','pending',0,1,1,0,?,'{}','{}','{}')`).run(JSON.stringify({ tone: '#d8cfbf', image_size: [47, 51] }));
    const row = db.prepare("INSERT INTO form_units(id,family,cluster,rank,similarity,image,split) VALUES(?,'U+3042','c',?,1,?,'0000000')");
    row.run('a', 0, '/atlas/media/a.webp');
    row.run('codh:1', 1, '/atlas/media/b.webp');
    const members = (db.query(membersQuery('typical')).all('c', 10, 0) as any[]).map(member);
    expect(members.map(m => [m.id, m.tone, m.image_size])).toEqual([['a', '#d8cfbf', [47, 51]], ['codh:1', null, null]]);
    db.close();
  });
});

describe('a form written as a description', () => {
  const setUp = () => {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    db.exec(`INSERT INTO form_families(code_point,char,label,count,cluster_count,forms,assigned,revision,rejected)
        VALUES('U+662F','是','是',3,2,'[{"char":"是"},{"char":"昰"}]',0,'r',0);
      INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,representatives) VALUES
        ('c1','U+662F','c1',2,1,0,0,'[]'),('c2','U+662F','c2',1,1,1,1,'[]');
      INSERT INTO form_units(id,family,cluster,rank,similarity,split) VALUES
        ('g1','U+662F','c1',0,1,''),('g2','U+662F','c1',1,1,''),('g3','U+662F','c2',0,1,'');
      INSERT INTO representations(id,scheme,value) VALUES('rp:a','ids','⿱日𤴓'),('rp:b','ids','⿱臼𤴓'),('rp:c','unicode','昰');
      INSERT INTO forms(id,anchor,created_by,created_at) VALUES('fm:a','rp:a','x','t'),('fm:b','rp:b','x','t'),('fm:c','rp:c','x','t');
      INSERT INTO current_claims(subject,predicate,scope,slot,status,object,members,supporting,claims,resolver,at) VALUES
        ('g3','has_form','','','asserted','fm:b','[]','[]','[]','r','t'),('elsewhere','has_form','','','asserted','fm:a','[]','[]','[]','r','t'),
        ('g2','has_form','','','asserted','fm:c','[]','[]','[]','r','t'),('g1','has_form','','','asserted','fm:a','[]','[]','[]','r','t');`);
    const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
      VALUES(?,'local','是','handwritten','kanji','pending',0,1,1,0,?,'{}','{}','{}')`);
    for (const id of ['g1', 'g2', 'g3']) unit.run(id, JSON.stringify({ image: id + '.webp' }));
    // g1's claim was made on a cut since replaced, so it no longer holds.
    db.exec(`UPDATE current_claims SET crop_version=(SELECT crop_version FROM units WHERE id=subject) WHERE subject IN ('g2','g3');
      UPDATE current_claims SET crop_version='old' WHERE subject='g1';`);
    const env = { DB: d1(db) } as unknown as Env;
    const tools = {
      fail: (status: number, message: string): never => { throw new Error(`${status} ${message}`) },
      body: async (request: Request) => request.json() as Promise<Record<string, unknown>>,
      text: (value: unknown) => value as string, codePoints: (value: string) => value,
      family: async (_: Env, char: string) => `U+${char.codePointAt(0)!.toString(16).toUpperCase()}`,
    };
    const ctx = { waitUntil: () => {} } as unknown as ExecutionContext;
    const decide = (body: object) => formsRoute(env, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx, 'r') as Promise<Record<string, any>>;
    // Each read goes past the edge cache, which a test has none of.
    (globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };
    const family = () => formsRoute(env, new Request('https://atlas.test/atlas/forms/families/U%2B662F'), '/atlas/forms/families/U%2B662F',
      new URLSearchParams(), tools, ctx) as Promise<Record<string, any>>;
    return { db, decide, family };
  };

  it('names a cluster and stays in the family it is named in', async () => {
    const { db, decide, family } = setUp();
    const decided = await decide({ kind: 'cluster', cluster: 'c1', form: '⿱日𤴓' });
    expect(decided.count).toBe(2);
    expect(db.query("SELECT form,written_family FROM form_units WHERE cluster='c1'").all()).toEqual([
      { form: '⿱日𤴓', written_family: 'U+662F' }, { form: '⿱日𤴓', written_family: 'U+662F' }]);
    expect(db.query('SELECT form,written_family FROM form_decisions').get()).toEqual({ form: '⿱日𤴓', written_family: 'U+662F' });
    // The palette offers it after the encoded forms, with the description a crop of the family is named
    // with in the crop dialog; one named elsewhere, on a replaced cut, or an encoded form is not offered.
    expect((await family()).described).toEqual([{ char: '⿱日𤴓', count: 2 }, { char: '⿱臼𤴓', count: 1 }]);
    db.close();
  });

  it('is refused when it is not well formed, as an encoded character outside the family is', async () => {
    const { db, decide } = setUp();
    await expect(decide({ kind: 'glyph', units: ['g1'], form: '⿱日' })).rejects.toThrow('422 ⿱日 is not a form of this family.');
    await expect(decide({ kind: 'glyph', units: ['g1'], form: '只' })).rejects.toThrow('422 只 is not a form of this family.');
    expect(db.query('SELECT count(*) AS n FROM form_decisions').get()).toEqual({ n: 0 });
    db.close();
  });

  it('is found from the descriptions, not from every crop\'s form claim', () => {
    const { db } = setUp();
    const plan = (db.query('EXPLAIN QUERY PLAN ' + describedClaimsQuery()).all('U+662F') as { detail: string }[]).map(r => r.detail);
    expect(plan.filter(line => line.startsWith('SEARCH representations'))).toEqual(
      Array(3).fill('SEARCH representations USING INDEX representation_value (value>? AND value<?)'));
    expect(plan).toContain('SEARCH c USING INDEX current_claim_object (predicate=? AND object=?)');
    expect(plan.filter(line => /^SCAN (?!r$)/.test(line))).toEqual([]);
    db.close();
  });
});
