// The admin writes: rejecting a reviewer's work and flagging corpus glyphs. The review writes they call
// (`revert`, `cropChanges`) belong to the review module; here they are stand-ins that record their calls
// and write the rows a crop change writes, so these tests hold what the admin routes decide.
import { afterEach, describe, expect, it, mock } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { cropId, publicId } from '../atlas3';
import { Problem } from '../busy';
import type { CropChange, Submission } from './review';

const reverted: [number, number, { by: string; reason: string; reviewer?: number[] } | undefined][] = [];
let blocked = new Set<number>();
mock.module('./review', () => ({
  revert: async (_env: Env, submission: Submission, actor: number, rejection?: { by: string; reason: string; reviewer?: number[] }) => {
    if (blocked.has(submission.id)) throw new Problem(409, 'A later review changed this crop. It cannot be undone.');
    reverted.push([submission.id, actor, rejection]);
    return { results: [] };
  },
  actorFor: async (env: Env, user: string) => (await env.ATLAS.prepare('SELECT id FROM actors WHERE key=?').bind(user).first<{ id: number }>())!.id,
  cropChanges: (env: Env, actor: number, key: string, at: number, changes: CropChange[]) => changes.flatMap(c => [
    env.ATLAS.prepare(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state,issue,named)
      SELECT ?1,revision+1,?2,0,?3,(SELECT id FROM submissions WHERE key=?4),?5,?6,(SELECT id FROM graphemes WHERE text=?7),?8,?9,NULL
      FROM crops WHERE id=?1 AND revision=?10 AND cut=?2`).bind(c.crop, c.cut, actor, key, at, c.kind, c.label, c.state, c.issue, c.expected),
    env.ATLAS.prepare('UPDATE crops SET state=?,revision=revision+1 WHERE id=? AND revision=?').bind(c.state, c.crop, c.expected)]),
  actorStats: () => { throw new Error('a flag counts no review') },
}));
const { flagCorpus, reject } = await import('./admin');

const tools = { text: (value: unknown, max: number, name: string, required = false) => {
  if (value == null && !required) return null;
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw new Problem(422, `Invalid ${name}.`);
  return value.trim();
} };
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); reverted.length = 0; blocked = new Set(); });
function atlas() {
  const db = new Database(':memory:'); databases.push(db);
  const dir = new URL('../../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(dir).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, dir), 'utf8'));
  db.exec(`INSERT INTO actors(id,key,user,via) VALUES(1,'admin','admin','account'),(2,'u1','u1','account'),(3,'reviewer-0000000a','u1','legacy'),(4,'u2','u2','account')`);
  return db;
}

describe('rejecting', () => {
  function submissions(db: Database) {
    const add = db.prepare('INSERT INTO submissions(id,key,actor,request,response,at,undone) VALUES(?,?,?,?,?,?,?)');
    add.run(1, 'u1:s1', 2, '{}', '{}', 10, 0); add.run(2, 'reviewer-0000000a:s2', 3, '{}', '{}', 20, 0); add.run(3, 'u1:s3', 2, '{}', '{}', 30, 1);
    add.run(4, 'u2:s4', 4, '{}', '{}', 40, 0); add.run(5, 'u1:s5', 2, '{}', '{}', 50, 0);
  }
  it('puts back the named submissions newest first, naming the ones a later review blocks', async () => {
    const db = atlas(); submissions(db);
    blocked = new Set([5]);
    const env = { ATLAS: d1(db) } as unknown as Env;
    const out = await reject(env, 'admin', { submissions: ['u1:s1', 'u1:s5', 'u1:s3', 'nope'], reason: 'spam' }, tools);
    expect(out).toEqual({ rejected: 1, conflicts: ['u1:s5'], unavailable: [], next: null, left: [] });
    expect(reverted).toEqual([[1, 1, { by: 'admin', reason: 'spam', reviewer: undefined }]]);
  });
  it('puts back all of one user\'s work across their ids, a page at a time', async () => {
    const db = atlas(); submissions(db);
    const env = { ATLAS: d1(db) } as unknown as Env;
    const out = await reject(env, 'admin', { user: 'u1' }, tools);
    expect(reverted.map(r => r[0])).toEqual([5, 2, 1]);
    expect(reverted[0][2]).toEqual({ by: 'admin', reason: '', reviewer: [2, 3] });
    expect(out.next).toBeNull();
    await expect(reject(env, 'admin', { submissions: [] }, tools)).rejects.toMatchObject({ status: 422 });
  });
  it('never takes up an admin\'s flag request: one named is refused, and one among a reviewer\'s is passed over', async () => {
    const db = atlas(); submissions(db);
    db.prepare('INSERT INTO submissions(id,key,actor,request,response,at,undone) VALUES(6,?,2,?,?,60,0)')
      .run('u1:f1', JSON.stringify({ target: '@flags', input: { finding: 'x', glyphs: [] } }), '{}');
    const env = { ATLAS: d1(db) } as unknown as Env;
    await expect(reject(env, 'admin', { submissions: ['u1:f1', 'u1:s1'] }, tools)).rejects.toMatchObject({ status: 409 });
    expect(reverted).toEqual([]);
    await reject(env, 'admin', { user: 'u1' }, tools);
    expect(reverted.map(r => r[0])).toEqual([5, 2, 1]);
    await expect(reject(env, 'admin', {}, tools)).rejects.toMatchObject({ status: 422 });
  });
});

describe('flagging corpus glyphs', () => {
  const P = 870101;
  it('flags glyphs nothing was written about, leaves the rest, and lists them in the order they were named', async () => {
    const db = atlas();
    db.exec(`INSERT INTO books(id,key) VALUES(1,'b'); INSERT INTO pages(id,key,book,rev) VALUES(${P},'p',1,1);
      INSERT INTO graphemes(id,text) VALUES(1,'と'),(2,'𛁈');`);
    const crop = (slot: number, kind: number, char: number, quiz = 1) =>
      db.prepare('INSERT INTO crops(id,kind,book,char,state,quiz,shuffle,revision) VALUES(?,?,1,?,0,?,0,3)').run(cropId(P, slot), kind, char, quiz);
    crop(0, 1, 1); crop(1, 1, 1); crop(2, 1, 2); crop(3, 1, 1, 0); crop(4, 0, 1); crop(5, 1, 1);
    // A round named glyph 1 earlier.
    db.prepare('INSERT INTO corpus_rows(crop,seq) VALUES(?,1)').run(cropId(P, 1));
    // An old text id of glyph 5, through the alias map.
    const shard = `v3/aliases/${createHash('sha256').update('hl:old:5').digest('hex').slice(0, 3)}.json.gz`;
    const bytes = gzipSync(JSON.stringify({ 'hl:old:5': cropId(P, 5) }));
    const MEDIA = { get: async (name: string) => name === shard ? { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null };
    const env = { ATLAS: d1(db), MEDIA } as unknown as Env;
    const id = (slot: number) => publicId(cropId(P, slot));
    const glyphs = [{ id: 'hl:old:5', character: 'と' }, { id: id(1), character: 'と' }, { id: id(2), character: 'と' }, { id: id(3), character: 'と' },
      { id: id(4), character: 'と' }, { id: 'hl:none:1', character: 'と' }, { id: id(0), character: 'と', realigned: '所', transcribed: 'シ' }];
    const out = await flagCorpus(env, 'admin', { finding: 'box-relabel', glyphs }, tools);
    expect(out).toMatchObject({ flagged: ['hl:old:5', id(0)], held: [id(1), id(4)], changed: [id(2)], unshowable: [id(3)], missing: ['hl:none:1'] });
    expect(db.query('SELECT crop,seq FROM corpus_rows ORDER BY seq').all()).toEqual([
      { crop: cropId(P, 1), seq: 1 }, { crop: cropId(P, 5), seq: 2 }, { crop: cropId(P, 0), seq: 3 }]);
    expect(db.query('SELECT crop,finding,realigned,transcribed FROM corpus_flags ORDER BY crop').all()).toEqual([
      { crop: cropId(P, 0), finding: 'box-relabel', realigned: '所', transcribed: 'シ' },
      { crop: cropId(P, 5), finding: 'box-relabel', realigned: null, transcribed: null }]);
    // Each flag is an event of kind 'flag' with the wrong-character issue, one revision on, under the admin's submission.
    expect(db.query(`SELECT e.crop,e.revision,k.key AS kind,e.state,e.issue,s.key AS submission FROM events e JOIN codes k ON k.field='kind' AND k.code=e.kind
      JOIN submissions s ON s.id=e.submission ORDER BY e.id`).all()).toMatchObject([
      { crop: cropId(P, 5), revision: 4, kind: 'flag', state: 2, issue: 0 }, { crop: cropId(P, 0), revision: 4, kind: 'flag', state: 2, issue: 0 }]);
    expect(JSON.parse((db.query('SELECT request FROM submissions').get() as { request: string }).request).target).toBe('@flags');
    // Sent again, every glyph is held.
    expect((await flagCorpus(env, 'admin', { finding: 'box-relabel', glyphs: glyphs.slice(0, 1) }, tools)).held).toEqual(['hl:old:5']);
  });
});
