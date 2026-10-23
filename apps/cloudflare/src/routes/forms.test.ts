import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { cropId, publicId } from '../atlas3';
import { FOLLOW_BATCH, describedClaimsQuery, clusterGlyphsQuery, clusterQuery, familyClustersQuery, familyGlyphsQuery, followCorpus, followStatements,
  formsRoute, glyphFamiliesQuery, listFollowQuery, memberDatesQuery, memberRowsQuery, recountQuery, stateQuery } from './forms';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });
function migrated(dir: string) {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL(`../../${dir}/`, import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
// Each read goes past the edge cache, which a test has none of.
(globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const tools = {
  fail: (status: number, message: string): never => { throw new Error(`${status} ${message}`) },
  body: async (request: Request) => request.json() as Promise<Record<string, any>>,
  text: (value: unknown) => value as string, codePoints: cp,
};

const PAGE_ID = 9000, HEAD = 12, TAIL = 24;
const FAMILY = 'U+306F', FORMS = JSON.stringify([{ char: 'は' }, { char: '𛂞' }, { char: '𛂙' }]);
// Two clusters of は: A with 40 glyphs, B with 3; every glyph a corpus glyph of one book.
const CLUSTERS = [{ key: 'U+306F/0', n: 1, size: 40, shape: 0, bySize: 1 }, { key: 'U+306F/1', n: 2, size: 3, shape: 1, bySize: 0 }];
type Glyph = { old: string; crop: number; cluster: typeof CLUSTERS[number]; rank: number; image: string; split: string };
const glyphs: Glyph[] = CLUSTERS.flatMap((c, ci) => Array.from({ length: c.size }, (_, rank) => {
  const slot = ci * 100 + rank;
  return { old: `codh:${c.n}-${rank}`, crop: cropId(PAGE_ID, slot), cluster: c, rank,
    image: slot.toString(16).padStart(64, '0'), split: Array.from({ length: 7 }, (_, k) => String((rank * 7 + k) % (k + 2))).join('') };
}));
const DATING = { kind: 'copied', start: 1700, end: 1710, precision: 'decade', text: '宝永', label: '1700s', status: 'single', calendar: 'jp' };

// The objects crop_facts.py writes: each cluster's list, and the family's index of each cluster's
// first and last glyphs.
function newDatabase() {
  const db = migrated('migrations-atlas'), objects = new Map<string, Uint8Array>();
  db.exec(`INSERT INTO generation(id,gen) VALUES(0,5); INSERT INTO books(id,key) VALUES(1,'hk:b');
    INSERT INTO graphemes(id,text,head) VALUES(1,'は',1),(2,'𛂞',1),(3,'𛂙',1);
    INSERT INTO pages(id,key,book,rev) VALUES(${PAGE_ID},'corpus:codh:hk:b:0',1,1);`);
  db.prepare(`INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source) VALUES(1,'witness',?,?,?,?,?,?,?,?,'s')`)
    .run(DATING.kind, DATING.start, DATING.end, DATING.precision, DATING.text, DATING.label, DATING.status, DATING.calendar);
  const index: Record<string, any> = { family: FAMILY, clusters: {} };
  for (const c of CLUSTERS) {
    const members = glyphs.filter(g => g.cluster === c).map(g => [g.crop, g.rank, 1 - g.rank / 100, g.image, g.split, null, null]);
    const list = `${c.n}-l${c.n}`;
    objects.set(`v3/forms/${list}.json.gz`, gzipSync(JSON.stringify({ family: FAMILY, key: c.key, members })));
    const look = members.map(m => [m[0], m[1], m[3], m[5], m[6]]);
    index.clusters[String(c.n)] = { list, count: members.length, head: look.slice(0, HEAD), tail: look.slice(Math.max(HEAD, look.length - TAIL)) };
    db.prepare(`INSERT INTO form_clusters(id,key,family,label,count,coherence,shape_position,size_position,list) VALUES(?,?,?,?,?,0.5,?,?,?)`)
      .run(c.n, c.key, FAMILY, c.key, c.size, c.shape, c.bySize, list);
  }
  objects.set(`v3/forms/families/${FAMILY}-i.json.gz`, gzipSync(JSON.stringify(index)));
  db.prepare(`INSERT INTO form_families(code_point,char,label,count,cluster_count,forms,assigned,rejected,revision,list) VALUES(?,'は','は',?,2,?,0,0,'r1',?)`)
    .run(FAMILY, glyphs.length, FORMS, `${FAMILY}-i`);
  for (const g of glyphs)
    db.prepare(`INSERT INTO crops(id,kind,book,char,cluster,state,quiz,shuffle) VALUES(?,1,1,1,?,0,1,?)`).run(g.crop, g.cluster.n, g.rank);
  db.exec(`UPDATE form_state SET lists='run-1'`);
  const bucket = { get: async (key: string) => objects.has(key) ? { body: new Response(objects.get(key)).body! } : null };
  return { db, env: { ATLAS: d1(db), MEDIA: bucket } as unknown as Env, objects };
}

// A glyph's text id in the fixture, as a decision below names it, becomes its crop's public id.
const byOld = new Map(glyphs.map(g => [g.old, publicId(g.crop)]));
const ctx = (pending: Promise<unknown>[] = []) => ({ waitUntil: (p: Promise<unknown>) => pending.push(p) }) as unknown as ExecutionContext;

function both() {
  const fresh = newDatabase();
  const get = async (path: string, query = '') => {
    const url = `https://atlas.test${path}${query ? '?' + query : ''}`, q = new URLSearchParams(query);
    const r = await formsRoute(fresh.env, new Request(url), decodeURI(path), q, tools, ctx());
    return r instanceof Response ? await r.json() : r;
  };
  const decide = async (body: Record<string, any>) => {
    const send = (b: object) => new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(b) });
    const pending: Promise<unknown>[] = [];
    const units = body.units?.map((u: string) => byOld.get(u));
    const b = await formsRoute(fresh.env, send(units ? { ...body, units } : body), '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(pending), 'u1') as any;
    await Promise.all(pending);
    const { id: _id, at: _at, ...rest } = b;
    return rest;
  };
  return { fresh, get, decide };
}

async function views(get: ReturnType<typeof both>['get']) {
  const paths: [string, string][] = [['/atlas/forms/families', ''], ['/atlas/forms/codes', ''],
    [`/atlas/forms/families/${encodeURIComponent(FAMILY)}`, 'order=shape'], [`/atlas/forms/families/${encodeURIComponent(FAMILY)}`, 'order=size']];
  for (const c of CLUSTERS) {
    const key = c.key.split('/').map(encodeURIComponent).join('/');
    paths.push([`/atlas/forms/clusters/${key}`, 'offset=0&limit=500&order=typical'], [`/atlas/forms/clusters/${key}`, 'offset=5&limit=10&order=unusual'],
      [`/atlas/forms/split/${key}`, 'k=2'], [`/atlas/forms/split/${key}`, 'k=4&shown=3']);
  }
  const found = [];
  for (const [path, query] of paths) found.push({ path, query, body: await get(path, query) });
  return found;
}

describe('the forms routes on glyph-atlas-v3', () => {
  // Each answer as the old database's routes gave it at the cutover, pinned in __snapshots__.
  it('keep their answers through cluster, glyph, report, mixed and inherit decisions, and corpus glyphs follow', async () => {
    const { fresh, get, decide } = both();
    const answers: any[] = [await views(get)];
    const steps: Record<string, any>[] = [
      { kind: 'cluster', cluster: 'U+306F/0', form: '𛂞' },
      { kind: 'glyph', units: ['codh:1-3', 'codh:1-20', 'codh:1-39'], form: '𛂙' },
      { kind: 'glyph', units: ['codh:1-25', 'codh:1-30'], issue: 'crop' },
      { kind: 'glyph', units: ['codh:1-26'], issue: 'character', character: '川' },
      { kind: 'cluster', cluster: 'U+306F/1', issue: 'mixed' },
      { kind: 'cluster', cluster: 'U+306F/1', form: 'は' },
      { kind: 'inherit', units: ['codh:1-20'] },
      { kind: 'cluster', cluster: 'U+306F/0', issue: 'character', character: '川' },
    ];
    // The views between decisions are pinned by digest, the last in full.
    for (const step of steps) answers.push({ step, decision: await decide(step),
      views: createHash('sha256').update(JSON.stringify(await views(get))).digest('hex') });
    answers.push(await views(get));
    expect(answers).toMatchSnapshot();
    // Every corpus glyph's character once the decisions are followed.
    const chars = Object.fromEntries((fresh.db.query('SELECT c.id,g.text FROM crops c LEFT JOIN graphemes g ON g.id=c.char').all() as any[])
      .map(r => [publicId(r.id), r.text]));
    expect(chars).toMatchSnapshot();
    expect(fresh.db.query('SELECT count(*) AS n FROM corpus_follow').get()).toEqual({ n: 0 });
    // The counts moved in the current generation: one cell per character, nothing left at zero.
    const tally = Object.fromEntries((fresh.db.query('SELECT g.text,t.n FROM tally t LEFT JOIN graphemes g ON g.id=t.char WHERE t.gen=5').all() as any[]).map(r => [r.text, r.n]));
    const counted = new Map<string, number>();
    for (const ch of Object.values(chars) as string[]) counted.set(ch, (counted.get(ch) ?? 0) + 1);
    expect(tally).toEqual(Object.fromEntries([...counted].filter(([ch]) => ch !== 'は').map(([ch, n]) => [ch, n])
      .concat([['は', (counted.get('は') ?? 0) - glyphs.length]]).filter(([, n]) => n !== 0)));
  });

  it('give a glyph its base character back when no decision names a form for it, and leave a reviewed one alone', async () => {
    const { fresh } = both();
    const first = glyphs[0], reviewed = glyphs[1];
    fresh.db.exec(`UPDATE crops SET revision=2 WHERE id=${reviewed.crop}`);
    const send = (body: object) => formsRoute(fresh.env, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1');
    await send({ kind: 'cluster', cluster: first.cluster.key, form: '𛂞' });
    await followCorpus(fresh.env);
    const char = (crop: number) => (fresh.db.query('SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(crop) as any).text;
    expect([char(first.crop), char(reviewed.crop)]).toEqual(['𛂞', 'は']);
    // A republish gave the glyph a new upstream character, which became its base (publish.py).
    fresh.db.exec(`INSERT INTO graphemes(id,text) VALUES(9,'ほ'); UPDATE form_bases SET char=9 WHERE crop=${first.crop}`);
    await send({ kind: 'cluster', cluster: first.cluster.key, form: null });
    await followCorpus(fresh.env);
    expect(char(first.crop)).toBe('ほ');
    expect(fresh.db.query('SELECT * FROM form_bases WHERE crop=?').all(first.crop)).toEqual([]);
    
  });

  it('follow a glyph a publication moved on but no one reviewed, and not one a review reached', async () => {
    const { db, env } = newDatabase(), [moved, reviewed] = [glyphs[0].crop, glyphs[1].crop];
    // One moved on by a publication event alone, one by a review.
    db.exec(`UPDATE crops SET revision=1 WHERE id IN (${moved},${reviewed});
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${moved},1,0,0,0,0,1,8),(${reviewed},1,0,0,1,1,1,0)`);
    await env.ATLAS.prepare(listFollowQuery()).bind(JSON.stringify([moved, reviewed])).run();
    expect(db.query('SELECT crop FROM corpus_follow').all()).toEqual([{ crop: moved }]);
  });

  it('keeps the source family and negative decade in form-follow tallies', async () => {
    const { db, env } = newDatabase(), crop = glyphs[0].crop;
    db.exec(`UPDATE crops SET char=NULL,family=1,year=-11 WHERE id=${crop};
      UPDATE form_clusters SET form='𛂞' WHERE id=1; INSERT INTO corpus_follow VALUES(${crop});`);
    await followCorpus(env);
    expect(db.query('SELECT char,family,decade,n FROM tally ORDER BY char').all())
      .toEqual([{ char: -1, family: 1, decade: -20, n: -1 }, { char: 2, family: 1, decade: -20, n: 1 }]);
    // The new character moves the glyph on a revision with a publication event; the store still counts none.
    expect(db.query('SELECT revision FROM crops WHERE id=?').get(crop)).toEqual({ revision: 1 });
    expect(db.query('SELECT revision,kind FROM events WHERE crop=?').all(crop)).toEqual([{ revision: 1, kind: 8 }]);
    db.exec(`UPDATE form_clusters SET form=NULL WHERE id=1; INSERT INTO corpus_follow VALUES(${crop});`);
    await followCorpus(env);
    expect(db.query('SELECT * FROM tally').all()).toEqual([]);
    expect(db.query('SELECT char FROM crops WHERE id=?').get(crop)).toEqual({ char: null });
  });

  it('offers description claims only on the effective crop cut and refreshes the palette', async () => {
    const { db, env } = newDatabase(), crop = glyphs[0].crop;
    const cache = new Map<string, Response>();
    (globalThis as any).caches = { default: { match: async (r: Request) => cache.get(r.url)?.clone(),
      put: async (r: Request, value: Response) => { cache.set(r.url, value); } } };
    const get = async () => {
      const pending: Promise<unknown>[] = [], path = '/atlas/forms/families/U%2B306F';
      const result = await formsRoute(env, new Request('https://atlas.test' + path), path, new URLSearchParams(), tools, ctx(pending)) as any;
      await Promise.all(pending); return result.described;
    };
    try {
      expect(await get()).toEqual([]);
      db.exec(`INSERT INTO representations(id,scheme,value) VALUES('r:ids','ids','⿱日𤴓');
        INSERT INTO forms VALUES('f:ids','r:ids','u','t');
        INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
          VALUES('claim-crop','has_form','','',${crop},0,'single','f:ids','[]','[]','[]','r','t');
        INSERT INTO ledger_submissions VALUES('claim','u','{}','{}','t');`);
      expect(await get()).toEqual([{ char: '⿱日𤴓', count: 1 }]);
      db.exec(`INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${crop},5,0,0,1,2,3,4);
        INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${crop},1,0,5,1,1,1,6);`);
      expect(await get()).toEqual([]);
      db.exec(`UPDATE current_claims SET cut=1; INSERT INTO ledger_submissions VALUES('claim2','u','{}','{}','t');`);
      expect(await get()).toEqual([{ char: '⿱日𤴓', count: 1 }]);
      db.exec(`UPDATE current_claims SET status='rejected'; INSERT INTO ledger_submissions VALUES('retract','u','{}','{}','t');`);
      expect(await get()).toEqual([]);
    } finally {
      (globalThis as any).caches = { default: { match: async () => undefined, put: async () => {} } };
    }
  });

  it('move a large decision in batches', async () => {
    const { fresh } = both();
    const many = Array.from({ length: FOLLOW_BATCH * 2 + 10 }, (_, i) => cropId(PAGE_ID + 1, i));
    fresh.db.exec(`INSERT INTO pages(id,key,book,rev) VALUES(${PAGE_ID + 1},'p2',1,1)`);
    for (const id of many) fresh.db.prepare('INSERT INTO crops(id,kind,book,char,cluster,state,quiz,shuffle) VALUES(?,1,1,1,1,0,1,0)').run(id);
    fresh.db.exec(`INSERT INTO corpus_follow(crop) SELECT id FROM crops WHERE id>=${cropId(PAGE_ID + 1, 0)};
      UPDATE form_clusters SET form='𛂞' WHERE id=1`);
    await followCorpus(fresh.env);
    expect(fresh.db.query(`SELECT count(*) AS n FROM crops WHERE char=2 AND id>=${cropId(PAGE_ID + 1, 0)}`).get()).toEqual({ n: many.length });
    expect(fresh.db.query('SELECT count(*) AS n FROM corpus_follow').get()).toEqual({ n: 0 });
  });

  it('refuse a decision while a load sends its rows, and one on a list a load replaced', async () => {
    const { fresh } = both();
    const send = (body: object) => formsRoute(fresh.env, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1');
    await expect(send({ kind: 'glyph', units: ['zzzz'], form: '𛂞' })).rejects.toThrow('422 Some glyphs are not in the current clustering.');
    await expect(send({ kind: 'glyph', units: [publicId(glyphs[0].crop)], form: '只' })).rejects.toThrow('422 只 is not a form of this family.');
    await expect(send({ kind: 'glyph', units: [publicId(glyphs[0].crop)], form: '⿱日𤴓' })).resolves.toMatchObject({ form: '⿱日𤴓', count: 1 });
    expect(fresh.db.query('SELECT written_family FROM form_glyphs').get()).toEqual({ written_family: FAMILY });
    const stale = await send({ kind: 'glyph', units: ['codh:unknown'], form: '𛂞' }) as Response;
    expect(stale.status).toBe(409);
    fresh.db.exec(`UPDATE form_state SET loading=1`);
    await expect(send({ kind: 'cluster', cluster: 'U+306F/0', form: '𛂞' })).rejects.toThrow('503 The forms are being republished.');
    fresh.db.exec(`UPDATE form_state SET loading=0`);
    // A load replaced the cluster's list after the decision read the cluster: the trigger refuses it.
    const atlas = d1(fresh.db), raced = { ...fresh.env, ATLAS: { ...atlas, batch: async (list: any[]) => {
      fresh.db.exec(`UPDATE form_clusters SET list='1-new' WHERE id=1`);
      return atlas.batch(list);
    } } } as unknown as Env;
    await expect(formsRoute(raced, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify({ kind: 'cluster', cluster: 'U+306F/0', form: '𛂞' }) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1')).rejects.toThrow('409 The cluster has changed; reload the forms.');
    expect(fresh.db.query(`SELECT count(*) AS n FROM form_decisions WHERE kind='cluster'`).get()).toEqual({ n: 0 });
  });

  it('keeps queued follows pending while clustering rows are being replaced', async () => {
    const { db, env } = newDatabase(), crop = glyphs[0].crop;
    db.exec(`UPDATE form_clusters SET form='𛂞' WHERE id=1;
      INSERT INTO corpus_follow VALUES(${crop}); UPDATE form_state SET loading=1;`);
    await followCorpus(env);
    expect(db.query('SELECT char FROM crops WHERE id=?').get(crop)).toEqual({ char: 1 });
    expect(db.query('SELECT crop FROM corpus_follow').all()).toEqual([{ crop }]);
    expect(db.query('SELECT * FROM form_bases').all()).toEqual([]);
    db.exec('UPDATE form_state SET loading=0');
    await followCorpus(env);
    expect(db.query('SELECT char FROM crops WHERE id=?').get(crop)).toEqual({ char: 2 });
    expect(db.query('SELECT * FROM corpus_follow').all()).toEqual([]);
  });

  it('refuses glyph and inherit decisions when a reload finishes after validation', async () => {
    for (const kind of ['glyph', 'inherit']) for (const change of [
      "UPDATE form_state SET lists='run-2'",
      "UPDATE form_families SET revision='r2'",
      `UPDATE crops SET cluster=NULL WHERE id=${glyphs[0].crop}`,
      "UPDATE form_clusters SET family='U+3042' WHERE id=1",
    ]) {
      const { db, env } = newDatabase(), atlas = d1(db);
      const raced = { ...env, ATLAS: { ...atlas, batch: async (list: any[]) => {
        db.exec(change); return atlas.batch(list);
      } } } as unknown as Env;
      const body = { kind, units: [publicId(glyphs[0].crop)], ...(kind === 'glyph' ? { form: '𛂞' } : {}) };
      await expect(formsRoute(raced, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
        '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1')).rejects.toThrow('409 The cluster has changed');
      expect(db.query('SELECT * FROM form_decisions').all()).toEqual([]);
      expect(db.query('SELECT * FROM form_glyphs').all()).toEqual([]);
      expect(db.query('SELECT * FROM corpus_follow').all()).toEqual([]);
    }
  });

  it('refuses a reload between membership validation and reading the allowed forms', async () => {
    for (const kind of ['glyph', 'inherit']) {
      const { db, env } = newDatabase(), atlas = d1(db);
      const raced = { ...env, ATLAS: { ...atlas, prepare: (sql: string) => {
        const statement = atlas.prepare(sql);
        if (sql !== glyphFamiliesQuery()) return statement;
        return { bind: (...args: any[]) => ({ first: async () => {
          const found = await statement.bind(...args).first();
          db.exec(`UPDATE crops SET cluster=2 WHERE id=${glyphs[0].crop};
            UPDATE form_families SET revision='r2'; UPDATE form_state SET lists='run-2';`);
          return found;
        } }) };
      } } } as unknown as Env;
      const body = { kind, units: [publicId(glyphs[0].crop)], ...(kind === 'glyph' ? { form: '𛂞' } : {}) };
      await expect(formsRoute(raced, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
        '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1')).rejects.toThrow('409 The cluster has changed');
      expect(db.query('SELECT * FROM form_decisions').all()).toEqual([]);
      expect(db.query('SELECT * FROM form_glyphs').all()).toEqual([]);
      expect(db.query('SELECT * FROM corpus_follow').all()).toEqual([]);
    }
  });

  it('asks for a reload when the validated family has disappeared', async () => {
    const { db, env } = newDatabase();
    db.exec('DELETE FROM form_families');
    await expect(formsRoute(env, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST',
      body: JSON.stringify({ kind: 'glyph', units: [publicId(glyphs[0].crop)], form: '𛂞' }) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1')).rejects.toThrow('409 The cluster has changed');
  });

  it('carry a cluster decision of an earlier clustering until the glyph\'s cluster is decided', async () => {
    const { fresh, get, decide } = both();
    const carriedGlyphs = glyphs.slice(0, 5), own = (g: Glyph) => publicId(g.crop);
    fresh.db.exec(`INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,crops,note)
      VALUES('old','2026-01-01T00:00:00+00:00','u0','cluster','${FAMILY}','𛂙','U+306F/old','r0','[]','[]','')`);
    for (const g of carriedGlyphs) fresh.db.prepare(`INSERT INTO form_glyphs(crop,family,cluster,form,issue,character,written_family,decision,carried)
      VALUES(?,?,1,'𛂙',NULL,NULL,?,'old','old')`).run(g.crop, FAMILY, FAMILY);
    fresh.db.prepare(recountQuery()).run(FAMILY);
    const family = async () => (await get(`/atlas/forms/families/${encodeURIComponent(FAMILY)}`, 'order=shape')) as any;
    const members = async () => new Map(((await get('/atlas/forms/clusters/U%2B306F/0', 'offset=0&limit=500')) as any).items
      .map((m: any) => [m.id, [m.form, m.basis]]));
    // The carried glyphs count as assigned, as their cluster's form, and are no exceptions.
    expect((await family()).assigned).toBe(5);
    expect((await family()).items[0]).toMatchObject({ assigned: 5, exceptions: 0, majority: '𛂙', majority_count: 5 });
    expect((await members()).get(own(glyphs[0]))).toEqual(['𛂙', 'form_cluster']);
    // A glyph decision overrides the carried one; following the cluster again restores it.
    const decided = await decide({ kind: 'glyph', units: [glyphs[0].old], form: 'は' });
    expect(decided.undo).toEqual([{ kind: 'inherit', units: [own(glyphs[0])] }]);
    expect((await members()).get(own(glyphs[0]))).toEqual(['は', 'form_glyph']);
    expect((await family()).items[0]).toMatchObject({ assigned: 5, exceptions: 1 });
    await decide({ kind: 'inherit', units: [glyphs[0].old] });
    expect((await members()).get(own(glyphs[0]))).toEqual(['𛂙', 'form_cluster']);
    expect(fresh.db.query('SELECT decision,carried FROM form_glyphs WHERE crop=?').get(glyphs[0].crop)).toEqual({ decision: 'old', carried: 'old' });
    // The cluster's own decision replaces the carried ones, also under a glyph's own decision.
    await decide({ kind: 'glyph', units: [glyphs[1].old], form: 'は' });
    await decide({ kind: 'cluster', cluster: 'U+306F/0', form: '𛂞' });
    expect(fresh.db.query('SELECT crop,form,carried FROM form_glyphs').all()).toEqual([{ crop: glyphs[1].crop, form: 'は', carried: null }]);
    expect((await members()).get(own(glyphs[0]))).toEqual(['𛂞', 'form_cluster']);
    await decide({ kind: 'inherit', units: [glyphs[1].old] });
    expect((await members()).get(own(glyphs[1]))).toEqual(['𛂞', 'form_cluster']);
    expect((await family()).assigned).toBe(40);
    // Every glyph a decision listed follows the form it ends with.
    const char = (crop: number) => (fresh.db.query('SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?').get(crop) as any).text;
    expect([char(glyphs[0].crop), char(glyphs[1].crop)]).toEqual(['𛂞', '𛂞']);
  });

  it('log each decision with the glyphs it names, a cluster decision with its list', async () => {
    const { fresh } = both();
    const send = (body: object) => formsRoute(fresh.env, new Request('https://atlas.test/atlas/forms/decisions', { method: 'POST', body: JSON.stringify(body) }),
      '/atlas/forms/decisions', new URLSearchParams(), tools, ctx(), 'u1');
    await send({ kind: 'cluster', cluster: 'U+306F/0', form: '𛂞' });
    await send({ kind: 'glyph', units: [publicId(glyphs[2].crop)], form: '𛂙' });
    const log = await formsRoute(fresh.env, new Request('https://atlas.test/atlas/forms/decisions.jsonl'), '/atlas/forms/decisions.jsonl',
      new URLSearchParams(), tools, ctx()) as Response;
    const lines = (await log.text()).trim().split('\n').map(line => JSON.parse(line));
    expect(lines.map(({ id: _i, at: _a, ...rest }) => rest)).toEqual([
      { actor: 'u1', kind: 'cluster', family: FAMILY, form: '𛂞', cluster: 'U+306F/0', revision: 'r1', units: [], note: '', list: '1-l1' },
      { actor: 'u1', kind: 'glyph', family: FAMILY, form: '𛂙', cluster: null, revision: 'r1', units: [publicId(glyphs[2].crop)], note: '' }]);
    expect(fresh.db.query('SELECT crops FROM form_decisions WHERE kind=?').get('glyph')).toEqual({ crops: JSON.stringify([glyphs[2].crop]) });
  });

  it('read every table by an index', () => {
    const { db } = newDatabase();
    // The state reads the first row of `form_families` and of `corpus_follow`.
    const state = (db.query('EXPLAIN QUERY PLAN ' + stateQuery()).all() as { detail: string }[]).map(r => r.detail);
    expect(state.filter(line => /^SCAN /.test(line))).toEqual(['SCAN form_families', 'SCAN corpus_follow']);
  const plans: [string, unknown[]][] = [ [familyClustersQuery('shape'), [FAMILY]], [familyClustersQuery('size'), [FAMILY]],
      [familyGlyphsQuery(), [FAMILY]], [describedClaimsQuery(), [FAMILY]], [clusterQuery(), ['k']], [memberRowsQuery(), ['[]']], [memberDatesQuery(), ['[]']],
      [clusterGlyphsQuery(), [1]], [glyphFamiliesQuery(), ['[]']], [recountQuery(), [FAMILY]], [listFollowQuery(), ['[]']],
      ...followStatements().map(sql => [sql, []] as [string, unknown[]])];
    for (const [sql, args] of plans) {
      const scans = (db.query('EXPLAIN QUERY PLAN ' + sql).all(...(args as any[])) as { detail: string }[]).map(r => r.detail)
        .filter(line => /^SCAN /.test(line) && !/^SCAN (r|j|json_each|CONSTANT ROW)\b/.test(line) && !/^SCAN (t|w|corpus_follow)\b/.test(line));
      expect({ sql, scans }).toEqual({ sql, scans: [] });
    }
  });
});
