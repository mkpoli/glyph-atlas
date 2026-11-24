import { nextPage } from './fixtures/pageIds';
import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { claimHistoryQuery, corpusRowQuery, currentClaimsQuery, currentCropClaimsQuery, ledgerActionsQuery, ledgerClaimsQuery, resolveClearQuery,
  resolveSlots, resolveWriteQuery } from '../ledger';
import { bookDateClaimsQuery, bookDates, bookDatesQuery, bookKeyQuery, claimRead, claimWrite, type ClaimTools, cropNowQuery, cropSlotsQuery, formNamesQuery,
  formOfCrop, ownClaimQuery, setForm, setForms, setGrapheme, ownStructuresQuery, setStructure, setFold, foldsOf, foldsQuery, ownFoldQuery, setWord, setWrittenFor, structureId, structureLabelsQuery, structuresOf, structuresQuery, claimedStructures, claimedStructuresQuery, wordMethodsQuery, wordNamesQuery, wordOfCrop,
  writtenForOf, writtenForQuery } from './claims';
import { IDS_DIGITS, referenceShard } from '../atlas3Reference';
import { familyClaims } from '../atlas3Gallery';

// The Workers cache, as far as alias reads use it.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

const W = 'ja/ばかり/副助詞', OTHER = 'ja/のみ/副助詞';
const BOX = { x: 0, y: 0, w: 10, h: 10 };
const atlasSchema = (db: Database, name = 'migrations-atlas') => {
  const folder = new URL(`../../${name}/`, import.meta.url);
  for (const file of readdirSync(folder).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, folder), 'utf8'));
};

function setup() {
  const db = new Database(':memory:'); databases.push(db);
  atlasSchema(db);
  // Each test takes a page and text ids of its own, since bundles and alias shards are held per isolate.
  const page = nextPage(), n = page;
  const local = cropId(page, 0), corpus = cropId(page, 1), retired = cropId(page, 2), gone = cropId(page, 3);
  const text = { local: `ex:${n}:a`, corpus: `hng:${n}:g`, retired: `ex:${n}:old`, unmapped: `ex:${n}:unmapped` };
  db.exec(`INSERT INTO books(id,key) VALUES(1,'book:${n}');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'book:${n}:1',1,3,4);
    INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(${local},0,1,0,1,1),(${corpus},1,1,0,1,2);
    INSERT INTO words VALUES('${W}','ja','ばかり','副助詞'),('${OTHER}','ja','のみ','副助詞');
    INSERT INTO actors(id,key,user) VALUES(1,'old-reader','reader');`);
  const aliases: Record<string, number> = { [text.local]: local, [text.corpus]: corpus, [text.retired]: retired };
  const shards = new Map<string, Record<string, number>>();
  for (const [t, id] of Object.entries(aliases)) {
    const name = `v3/aliases/${createHash('sha256').update(t).digest('hex').slice(0, 3)}.json.gz`;
    shards.set(name, { ...shards.get(name), [t]: id });
  }
  const objects = new Map<string, Uint8Array>([...shards].map(([name, shard]) => [name, gzipSync(JSON.stringify(shard))]));
  objects.set(bundleKey(page, 3), gzipSync(JSON.stringify({ key: `book:${n}:1`, book: 1, rev: 3, page: null, crops: [
    { slot: 0, id: text.local, image_sha256: 'px', box: BOX },
    { slot: 1, id: text.corpus, source_revision: 'rev1', box: BOX },
    { slot: 2, id: text.retired, retired: true, replaced_by: local },
    { slot: 3, id: `ex:${n}:orphan`, retired: true }] })));
  const reads: string[] = [];
  const MEDIA = { get: async (name: string) => {
    reads.push(name);
    const bytes = objects.get(name);
    return bytes ? { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer } : null;
  } };
  const runs = new Database(':memory:'); databases.push(runs);
  atlasSchema(runs, 'migrations-atlas-runs');
  const env = { ATLAS: d1(db), ATLAS_RUNS: d1(runs), MEDIA } as unknown as Env;
  const tools: ClaimTools = {
    fail: (status, message, extra): never => { throw Object.assign(new Error(`${status} ${message}`), { extra }); },
    body: async request => await request.json(), canonical: JSON.stringify, literal: value => value,
    text: (value, _max, name, required) => {
      if (value == null && !required) return null;
      if (typeof value !== 'string' || (required && !value.trim())) throw new Error(`422 Invalid ${name}.`);
      return value;
    },
    integer: (q, key, fallback) => Number(q.get(key) ?? fallback),
  };
  const version = (id: number, pixels = 'px') => `${publicId(id)}@${pixels}@0,0,10,10`;
  const input = (value = {}) => ({ id: crypto.randomUUID(), crop_version: version(local), word: W, method: 'ruby', form: 'certain', ...value });
  const save = (value = {}, actor = 'reader') => setWord(env, input(value), publicId(local), actor, tools);
  const post = (path: string, body: unknown, me = { id: 'reader', admin: false }) => claimWrite(env,
    new Request('https://x' + path, { method: 'POST', body: JSON.stringify(body) }), path, me, tools, async () => {});
  const claim = (members: unknown[], actor: string, method = 'context', crop = local) => post('/atlas/claims',
    { id: crypto.randomUUID(), crop_version: version(crop, crop === corpus ? 'rev1' : 'px'), subject: publicId(crop), predicate: 'writes_word', method, claims: members },
    { id: actor, admin: false }) as Promise<any>;
  const read = (path: string) => claimRead(env, new URL('https://x' + path), tools);
  return { db, runs, env, tools, text, local, corpus, retired, gone, version, input, save, post, claim, read, reads, page, objects };
}

describe('the crop word picker', () => {
  it('saves once, replaces its own word, and clears only its own claim', async () => {
    const { db, env, tools, local, input, save } = setup();
    const first = input(), answer = await setWord(env, first, publicId(local), 'reader', tools);
    expect(answer.id).toBe(publicId(local));
    expect(answer.replaced).toBeNull();
    expect(answer.word).toMatchObject({ status: 'asserted', values: [{ word: W, reading: 'ばかり', class: '副助詞', form: 'certain' }], methods: ['ruby'], by: ['reader'] });
    expect(await setWord(env, first, publicId(local), 'reader', tools)).toEqual(answer);
    expect(db.query('SELECT subject,crop,cut FROM assertions').all()).toEqual([{ subject: expect.stringMatching(/^ex:/), crop: local, cut: 0 }]);
    expect(db.query("SELECT ref,crop,cut FROM assertion_evidence WHERE kind='crop'").get()).toEqual({ ref: `${publicId(local)}@px@0,0,10,10`, crop: local, cut: 0 });
    await expect(setWord(env, { ...first, word: OTHER }, publicId(local), 'reader', tools)).rejects.toThrow('409');
    const replaced = await save({ word: OTHER, form: 'probable', method: 'context' });
    expect(replaced.replaced).toEqual({ word: W, method: 'ruby', form: 'certain' });
    expect(replaced.word?.values).toEqual([{ word: OTHER, reading: 'のみ', class: '副助詞', form: 'probable' }]);
    await save({}, 'other');
    const cleared = await save({ word: null });
    expect(cleared.replaced).toEqual({ word: OTHER, method: 'context', form: 'probable' });
    expect(cleared.word?.by).toEqual(['other']);
    await expect(save({ word: null })).rejects.toThrow('409 You have no word on this crop to clear.');
    const clear = input({ word: null });
    const last = await setWord(env, clear, publicId(local), 'other', tools);
    expect(last.word).toBeNull();
    expect(await setWord(env, clear, publicId(local), 'other', tools)).toEqual(last);
    // A claim changes no review state.
    expect(db.query('SELECT revision,state FROM crops WHERE id=?').get(local)).toEqual({ revision: 0, state: 0 });
  });

  it('recognises an earlier actor key belonging to the same reader', async () => {
    const { save } = setup();
    await save({}, 'old-reader');
    const result = await save({ word: null });
    expect(result.word).toBeNull();
    expect(result.replaced?.word).toBe(W);
  });

  it('refuses unknown words, missing evidence, invalid confidence and other versions before writing', async () => {
    const { db, local, save } = setup();
    for (const word of ['missing', {}, undefined]) await expect(save({ word })).rejects.toThrow('422 No such word.');
    for (const method of [undefined, '', 'guess']) await expect(save({ method })).rejects.toThrow('422');
    for (const form of [undefined, 'maybe', 1]) await expect(save({ form })).rejects.toThrow('422');
    await expect(save({ crop_version: `${publicId(local)}@old@0,0,10,10` })).rejects.toThrow('409 This crop was cut again. Reload it.');
    expect(db.query('SELECT count(*) AS n FROM ledger_submissions').get()).toEqual({ n: 0 });
    await save();
    // A redrawn box waiting for the next publication is the version a reviewer sees now.
    db.exec(`INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${local},0,0,0,1,2,3,4)`);
    await expect(save({ word: null })).rejects.toThrow('409 This crop was cut again. Reload it.');
    expect((await save({ word: null, crop_version: `${publicId(local)}@px@1,2,3,4` })).word).toBeNull();
  });

  it('reads glosses, alternatives, disputed standing values and the strongest supporting methods', async () => {
    const { db, env, local, save, claim } = setup();
    await claim([{ value: { gloss: 'only this much' }, confidence: 0.5, confidence_scheme: 'form' }], 'glosser');
    expect((await wordOfCrop(env, local))?.values).toEqual([{ gloss: 'only this much', form: 'probable' }]);
    await save({ word: null }, 'glosser');
    await save();
    const disputed = await save({ word: OTHER, form: 'probable', method: 'note' }, 'other');
    expect(disputed.word).toMatchObject({ status: 'disputed', supporting: [], methods: ['ruby', 'note'] });
    // Competing claims can share a millisecond; their generated ids break the tie in either order.
    expect(disputed.word.values).toHaveLength(2);
    expect(disputed.word.values).toEqual(expect.arrayContaining([
      { word: W, reading: 'ばかり', class: '副助詞', form: 'certain' }, { word: OTHER, reading: 'のみ', class: '副助詞', form: 'probable' },
    ]));
    expect([...disputed.word.by].sort()).toEqual(['other', 'reader']);
    await save({ word: null }, 'other');
    await save({ method: 'context' }, 'context');
    await save({ method: 'gloss' }, 'gloss');
    await save({ method: 'ruby' }, 'ruby');
    expect((await wordOfCrop(env, local))?.methods).toEqual(['gloss', 'ruby', 'context']);
    // A rejected competing claim contributes neither a value nor a method nor an asserter.
    const different = await claim([{ object: OTHER, confidence: 1, confidence_scheme: 'form' }], 'rejected', 'note');
    db.query("INSERT INTO assertion_actions(id,assertion,action,actor,at) VALUES('reject',?,'reject','voter','now')").run(different.assertions[0]);
    const subject = (db.query('SELECT subject FROM assertions LIMIT 1').get() as { subject: string }).subject;
    await env.ATLAS.batch(resolveSlots(env, [[subject, 'writes_word', '', '']]));
    const current = (await wordOfCrop(env, local))!;
    expect(current.values.map((v: any) => v.word)).toEqual([W]);
    expect(current.by).not.toContain('rejected');
    expect(current.methods).not.toContain('note');
    for (const actor of ['reader', 'context', 'gloss', 'ruby']) await save({ word: null }, actor);
    await claim([W, OTHER].map(object => ({ object, confidence: 1, confidence_scheme: 'form' })), 'alternatives');
    expect((await wordOfCrop(env, local))?.values).toHaveLength(2);
    await save({ word: null }, 'alternatives');
    expect((await wordOfCrop(env, local))?.values).toEqual([]);
  });
});

describe('the crop grapheme picker', () => {
  const AFTER = 'U+5F8C', QUEEN = 'U+540E';
  // 後 and 后 head families of their own; 从 is filed under 從.
  function graphemes() {
    const found = setup();
    found.db.exec(`INSERT INTO graphemes(id,text,head) VALUES(1,'後',1),(2,'后',2),(3,'從',3),(4,'从',3);
      UPDATE crops SET char=2 WHERE id=${found.local};`);
    const pick = (grapheme: string | null, actor = 'reader', id: string = crypto.randomUUID()) =>
      setGrapheme(found.env, { id, crop_version: found.version(found.local), grapheme }, publicId(found.local), actor, found.tools);
    return { ...found, pick };
  }

  it('files the crop under the grapheme it is read as, and back under its character\'s once taken back', async () => {
    const { env, local, pick } = graphemes(), id = crypto.randomUUID();
    const saved = await pick(AFTER, 'reader', id);
    expect(saved).toEqual({ id: publicId(local), grapheme_claim: { status: 'asserted', values: [{ grapheme: AFTER, char: '後', by: ['reader'] }], by: ['reader'] }, replaced: null });
    expect(await pick(AFTER, 'reader', id)).toEqual(saved);
    expect((await familyClaims(env, AFTER, [1])).here.map(r => r.id)).toEqual([local]);
    expect((await familyClaims(env, QUEEN, [2])).away.map(r => r.id)).toEqual([local]);
    expect(await pick(null)).toMatchObject({ grapheme_claim: null, replaced: AFTER });
    expect(await familyClaims(env, QUEEN, [2])).toEqual({ away: [], here: [] });
  });

  it('files a disputed crop under its character\'s grapheme', async () => {
    const { env, pick } = graphemes();
    await pick(AFTER);
    const disputed = await pick(QUEEN, 'other');
    expect(disputed.grapheme_claim?.status).toBe('disputed');
    expect(disputed.grapheme_claim?.values.map((v: { char: string }) => v.char).sort()).toEqual(['后', '後']);
    expect(await familyClaims(env, QUEEN, [2])).toEqual({ away: [], here: [] });
  });

  it('records a claim under the head its key is filed under, and compares what is sent again as it was sent', async () => {
    const { db, local, version, pick, post } = graphemes();
    // 𽌇 headed itself when this pick was kept on a device; by the time it is sent it is filed under 春.
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(5,'春',5),(6,'\u{3D307}',5);`);
    const id = crypto.randomUUID(), kept = await pick('U+3D307', 'reader', id);
    expect(kept.grapheme_claim).toMatchObject({ status: 'asserted', values: [{ grapheme: 'U+6625', char: '春' }] });
    expect(await pick('U+3D307', 'reader', id)).toEqual(kept);
    await expect(pick('U+6625', 'reader', id)).rejects.toThrow('409');
    const again = crypto.randomUUID(), claim = (object: string, named: string = crypto.randomUUID()) => post('/atlas/claims', { id: named,
      crop_version: version(local), subject: publicId(local), predicate: 'has_grapheme', claims: [{ object }] }) as Promise<any>;
    const made = await claim('U+3D307', again);
    expect(made.current[0]).toMatchObject({ predicate: 'has_grapheme', status: 'asserted', object: 'U+6625' });
    expect(await claim('U+3D307', again)).toEqual(made);
  });

  it('names only a grapheme the site holds, on the generic route too', async () => {
    const { local, version, pick, post } = graphemes();
    await expect(pick('U+9999')).rejects.toThrow('422 No such character.');
    await expect(pick('後')).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(pick('U+005F8C')).rejects.toThrow('422 Name a grapheme by its code points.');
    for (const key of ['U+110000', 'U+D800', 'U+FFFFFF']) await expect(pick(key)).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(pick(null)).rejects.toThrow('409 You have no grapheme on this crop to clear.');
    const claim = (object: string) => post('/atlas/claims', { id: crypto.randomUUID(), crop_version: version(local), subject: publicId(local),
      predicate: 'has_grapheme', claims: [{ object }] }) as Promise<any>;
    await expect(claim('U+005F8C')).rejects.toThrow('422 U+005F8C cannot be the object of has_grapheme.');
    await expect(claim('U+110000')).rejects.toThrow('422 U+110000 cannot be the object of has_grapheme.');
    expect((await claim(AFTER)).current[0]).toMatchObject({ predicate: 'has_grapheme', status: 'asserted', object: AFTER });
  });

  it('files the crop under the family its grapheme is filed under after a publication moves it', async () => {
    const { db, env, local, pick } = graphemes();
    await pick(AFTER);
    // 後 joins 從's family: the crop read as 後 is listed with 從, and still not with 后.
    db.exec('UPDATE graphemes SET head=3 WHERE id=1');
    expect((await familyClaims(env, 'U+5F9E', [3, 4, 1])).here.map(r => r.id)).toEqual([local]);
    expect((await familyClaims(env, QUEEN, [2])).away.map(r => r.id)).toEqual([local]);
  });

  it('files the crop\'s runs under the grapheme it is read as, and back once taken back', async () => {
    const { db, runs, local, corpus, pick } = graphemes();
    // A run 後后 whose 后 is the crop.
    db.exec(`UPDATE crops SET char=1 WHERE id=${corpus}`);
    runs.exec(`INSERT INTO runs VALUES(2,'後后',0,1,${corpus},${local},NULL,1); INSERT INTO run_stats VALUES(2,'後后',1,1)`);
    const held = () => [runs.query('SELECT graphemes FROM runs').all(), runs.query('SELECT graphemes,n FROM run_stats').all(), runs.query('SELECT crop,char FROM run_written').all()];
    await pick(AFTER);
    expect(held()).toEqual([[{ graphemes: '後後' }], [{ graphemes: '後後', n: 1 }], [{ crop: local, char: '后' }]]);
    await pick(null);
    expect(held()).toEqual([[{ graphemes: '後后' }], [{ graphemes: '後后', n: 1 }], []]);
  });

  it('answers a saved claim as saved when its runs cannot be filed', async () => {
    const { env, local, pick } = graphemes();
    (env as any).ATLAS_RUNS = { prepare: () => { throw new Error('D1_ERROR: runs unavailable') } };
    const saved = await pick(AFTER);
    expect(saved.grapheme_claim?.values.map((v: { grapheme: string }) => v.grapheme)).toEqual([AFTER]);
    expect((await familyClaims(env, AFTER, [1])).here.map(r => r.id)).toEqual([local]);
  });

  it('goes through the picker route', async () => {
    const { local, version, post } = graphemes();
    const answer = await post(`/atlas/characters/${publicId(local)}/grapheme`, { id: crypto.randomUUID(), crop_version: version(local), grapheme: AFTER }) as any;
    expect(answer.grapheme_claim.values[0].grapheme).toBe(AFTER);
  });
});

describe('the crop form picker', () => {
  it('names a typed value once, claims it on the crop and takes it back', async () => {
    const { db, env, tools, local, version } = setup();
    const pick = (form: string | null, actor = 'reader') => setForm(env, { id: crypto.randomUUID(), crop_version: version(local), form }, publicId(local), actor, tools);
    const first = await pick('⿺辶𦊷');
    expect(first).toMatchObject({ id: publicId(local), replaced: null,
      form: { status: 'asserted', values: [{ form: 'fm:9c8fb02868fdcbc3357a0b63ab17f5f4', scheme: 'ids', text: '⿺辶𦊷', confidence: null }], by: ['reader'] } });
    expect(db.query('SELECT id,scheme,value FROM representations').all()).toEqual([{ id: 'rp:4fd4eff3084f8bbbe5489e119eea2862', scheme: 'ids', value: '⿺辶𦊷' }]);
    expect(db.query("SELECT subject,crop,cut,object FROM current_claims WHERE predicate='represented_by'").get())
      .toEqual({ subject: 'fm:9c8fb02868fdcbc3357a0b63ab17f5f4', crop: null, cut: null, object: 'rp:4fd4eff3084f8bbbe5489e119eea2862' });
    await pick('⿺辶𦊷', 'other');
    expect(db.query('SELECT count(*) AS n FROM forms').get()).toEqual({ n: 1 });
    expect((await pick('還')).replaced).toBe('⿺辶𦊷');
    const cleared = await pick(null);
    expect(cleared.replaced).toBe('還');
    expect(cleared.form?.values.map((v: any) => v.text)).toEqual(['⿺辶𦊷']);
    await expect(pick(null)).rejects.toThrow('409 You have no form on this crop to clear.');
    await expect(pick('⿰木')).rejects.toThrow('422 This description is missing a component.');
    expect(await formOfCrop(env, local)).toMatchObject({ by: ['other'] });
  });
});

describe('a form written for a grapheme', () => {
  const AFTER = 'U+5F8C', QUEEN = 'U+540E';
  // 後 and 后 head families of their own; 从 is filed under 從. A described form of the local crop is
  // the form these claims are about.
  async function forms() {
    const found = setup();
    found.db.exec(`INSERT INTO graphemes(id,text,head) VALUES(1,'後',1),(2,'后',2),(3,'從',3),(4,'从',3);`);
    const made = await setForm(found.env, { id: crypto.randomUUID(), crop_version: found.version(found.local), form: '⿺辶𦊷' },
      publicId(found.local), 'reader', found.tools);
    const form = made.form!.values[0].form!;
    const add = (grapheme: string | null, remove = false, actor = 'reader', id = crypto.randomUUID()) =>
      setWrittenFor(found.env, { id, grapheme, remove }, form, actor, found.tools);
    return { ...found, form, add };
  }

  it('names a form as written for one grapheme and then another, and takes one claim back', async () => {
    const { db, form, add } = await forms();
    const saved = await add(AFTER);
    expect(saved).toMatchObject({ id: form, written_for: [{ grapheme: AFTER, char: '後', by: ['reader'] }], replaced: null });
    expect(db.query("SELECT subject,crop,cut,object FROM current_claims WHERE predicate='written_for'").get())
      .toEqual({ subject: form, crop: null, cut: null, object: AFTER });
    expect(await add(AFTER)).toEqual(saved);
    await add(QUEEN);
    expect((await add(AFTER, true)).written_for.map((v: any) => v.grapheme)).toEqual([QUEEN]);
    await expect(add(AFTER, true)).rejects.toThrow('409 You have not said this form writes 後.');
  });

  it('records a form under the head its key is filed under, and takes it back by either', async () => {
    const { db, form, add } = await forms();
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(5,'春',5),(6,'\u{3D307}',5);`);
    const id = crypto.randomUUID(), spring = { grapheme: 'U+6625', char: '春', by: ['reader'] };
    expect(await add('U+3D307', false, 'reader', id)).toEqual({ id: form, grapheme: 'U+6625', written_for: [spring], replaced: null });
    expect(await add('U+3D307', false, 'reader', id)).toEqual({ id: form, grapheme: 'U+6625', written_for: [spring], replaced: null });
    await expect(add('U+6625', false, 'reader', id)).rejects.toThrow('409');
    // The page the claim was made on still names 𽌇.
    expect(await add('U+3D307', true)).toMatchObject({ written_for: [], replaced: 'U+6625' });
    // A claim is taken back by its own key whatever the table holds of it.
    await add(AFTER);
    db.exec('DELETE FROM graphemes WHERE id=1');
    expect(await add(AFTER, true)).toMatchObject({ written_for: [], replaced: AFTER });
  });

  it('takes back only the reader\'s own claim, and names only a grapheme the site holds', async () => {
    const { db, env, tools, form, add } = await forms();
    await add(AFTER);
    await add(AFTER, false, 'other');
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='written_for'").get()).toEqual({ n: 2 });
    expect((await add(AFTER, true, 'other')).written_for).toEqual([{ grapheme: AFTER, char: '後', by: ['reader'] }]);
    await expect(add(AFTER, true, 'third')).rejects.toThrow('409 You have not said this form writes 後.');
    await expect(add('U+9999')).rejects.toThrow('422 No such character.');
    await expect(add('後')).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(add('U+005F8C')).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(setWrittenFor(env, { id: crypto.randomUUID(), grapheme: AFTER }, 'fm:' + '0'.repeat(32), 'reader', tools))
      .rejects.toThrow('404 No such form.');
  });

  it('reads the form\'s graphemes from its current claims, not from every claim ever made', async () => {
    const { db, env, form, add } = await forms();
    await add(AFTER);
    db.exec(`INSERT INTO assertion_actions(id,assertion,action,actor,at)
      SELECT 'reject',id,'reject','voter','now' FROM assertions WHERE predicate='written_for'`);
    await env.ATLAS.batch(resolveSlots(env, [[form, 'written_for', '', AFTER]]));
    expect(await writtenForOf(env, form)).toEqual([]);
    expect(db.query(writtenForQuery()).all(form)).toEqual([expect.objectContaining({ status: 'rejected' })]);
  });
});

describe('a form filed under a structure', () => {
  const SOURCE = { scheme: 'babelstone-ids', version: '2025-06-27', policy: 'structure-v1' };
  // 峰 and 峯 are two structures of one character; 丑 has none. The reference objects are held per
  // isolate, so every test writes the same ones.
  async function forms() {
    const found = setup();
    const beside = await structureId('⿰山夆', SOURCE.scheme, SOURCE.policy), above = await structureId('⿱山夆', SOURCE.scheme, SOURCE.policy);
    const wind = await structureId('⿱山風', SOURCE.scheme, SOURCE.policy);
    const rows: Record<string, unknown[]> = { '峰': [['⿰山夆'], null, [beside, '⿰山夆']], '峯': [['⿱山夆'], null, [above, '⿱山夆']],
      '丑': [[], null, null], '岑': [['⿱山今'], null, ['st:' + '0'.repeat(32), '⿱山今']], '嵐': [['⿱山風'], null, [wind, '⿱山風']],
      // The seal character of 遇.
      '𽓻': [[], null, [await structureId('⿰辵禺', SOURCE.scheme, SOURCE.policy), '⿰辵禺']],
      // 嶺's regional shapes are spellings of its structure, even the one that is 峯's own.
      '嶺': [['⿱山領'], [{ text: '⿱山領', regions: 'GT' }, { text: '⿱山夆', regions: 'J' }], [beside, '⿰山夆']],
      // A description the load read: the form's own, which spells 峰 with another drawing of 夆.
      '⿰山夅': [['⿰山夅'], null, [beside, '⿰山夆']] };
    const shards = new Map<string, Record<string, unknown>>();
    for (const [char, row] of Object.entries(rows)) {
      const name = `v3/reference/ids/${await referenceShard(char, IDS_DIGITS)}.json.gz`;
      shards.set(name, { ...shards.get(name), [char]: row });
    }
    for (const [name, shard] of shards) found.objects.set(name, gzipSync(JSON.stringify(shard)));
    found.objects.set('v3/reference/structure.json.gz', gzipSync(JSON.stringify(SOURCE)));
    const made = await setForm(found.env, { id: crypto.randomUUID(), crop_version: found.version(found.local), form: '⿰山夆' },
      publicId(found.local), 'reader', found.tools);
    const form = made.form!.values[0].form!;
    const say = (char: string, remove = false, actor = 'reader', id = crypto.randomUUID()) =>
      setStructure(found.env, { id, char, ...(remove ? { remove } : {}) }, form, actor, found.tools);
    const fold = (from: Record<string, unknown>, into: Record<string, unknown> | null, actor = 'reader', id = crypto.randomUUID()) =>
      setFold(found.env, { id, from, ...(into ? { into } : { remove: true }) }, actor, found.tools);
    return { ...found, form, say, fold, beside, above, wind };
  }

  it('derives the id glyph_atlas.structures gives a label', async () => {
    // structures.for_character("峰").id
    expect(await structureId('⿰山夆', SOURCE.scheme, SOURCE.policy)).toBe('st:7cbba6f444213f31ed4ff4c58fb0ccb5');
  });

  it('names a structure by a character that has it, writes its row once, and takes one claim back', async () => {
    const { db, form, say, beside, above } = await forms();
    const saved = await say('峰');
    expect(saved).toEqual({ id: form, structures: [{ structure: beside, label: '⿰山夆', by: ['reader'] }], replaced: [] });
    expect(db.query('SELECT id,label,scheme,version,policy,created_by FROM structures').all())
      .toEqual([{ id: beside, label: '⿰山夆', ...SOURCE, created_by: 'reader' }]);
    expect(db.query("SELECT subject,crop,object,method FROM assertions WHERE predicate='has_structure'").get())
      .toEqual({ subject: form, crop: null, object: beside, method: 'form-picker' });
    expect(await say('峰')).toEqual(saved);
    await say('峰', false, 'other');
    expect(db.query('SELECT count(*) AS n FROM structures').get()).toEqual({ n: 1 });
    expect((await say('峯')).structures.map((v: any) => v.label).sort()).toEqual(['⿰山夆', '⿱山夆']);
    expect((await say('峰', true)).replaced).toEqual([beside]);
    expect((await say('峰', true, 'other')).structures).toEqual([{ structure: above, label: '⿱山夆', by: ['reader'] }]);
    await expect(say('峰', true)).rejects.toThrow('409 You have not said this form has this structure.');
  });

  it('names a structure by a description the load read, and by a character with its variation selector', async () => {
    const { form, say, beside } = await forms();
    expect(await say('⿰山夅')).toEqual({ id: form, structures: [{ structure: beside, label: '⿰山夆', by: ['reader'] }], replaced: [] });
    const both = (await say('峰\u{E0100}', false, 'other')).structures;
    expect(both.map((v: any) => [v.structure, [...v.by].sort()])).toEqual([[beside, ['other', 'reader']]]);
  });

  it('names by a layout the structure it stands under, which no claim has named', async () => {
    const { env, tools, form } = await forms();
    const name = (char: string) => setStructure(env, { id: crypto.randomUUID(), char, head: true }, form, 'reader', tools);
    // structures.for_character("遇").id
    expect((await name('𽓻')).structures).toEqual([{ structure: 'st:8809517f55e2790006eb57615ce80656', label: '⿺辶禺', by: ['reader'] }]);
    await expect(name('峰')).rejects.toThrow('422 峰 is no layout of another structure.');
  });

  it('takes a claim back by the id of its structure, and refuses one id for two requests', async () => {
    const { env, tools, form, say, beside } = await forms();
    const id = crypto.randomUUID();
    await say('峰', false, 'reader', id);
    await expect(say('峯', false, 'reader', id)).rejects.toThrow('409');
    const taken = await setStructure(env, { id: crypto.randomUUID(), structure: beside, remove: true }, form, 'reader', tools);
    expect(taken).toEqual({ id: form, structures: [], replaced: [beside] });
    await expect(setStructure(env, { id: crypto.randomUUID(), structure: 'st:x', remove: true }, form, 'reader', tools))
      .rejects.toThrow('422 Name a structure by its id.');
  });

  it('refuses a character with no structure, a mismatched id, and a form that is not there', async () => {
    const { env, tools, say } = await forms();
    await expect(say('丑')).rejects.toThrow('422 No structure is known for 丑.');
    await expect(say('鑫')).rejects.toThrow('422 No structure is known for 鑫.');
    await expect(say('峰峯')).rejects.toThrow('422 Name one character or description.');
    await expect(say('⿰山')).rejects.toThrow('422 Name one character or description.');
    await expect(say('⿰山今')).rejects.toThrow('422 No structure is known for ⿰山今.');
    await expect(say('岑')).rejects.toThrow('503 The structures are being loaded.');
    await expect(setStructure(env, { id: crypto.randomUUID(), char: '峰' }, 'fm:' + '0'.repeat(32), 'reader', tools))
      .rejects.toThrow('404 No such form.');
  });

  it('names a structure a claim has named by its id', async () => {
    const { env, tools, form, say, beside, above } = await forms();
    await expect(setStructure(env, { id: crypto.randomUUID(), structure: above }, form, 'reader', tools)).rejects.toThrow('422 No claim has named this structure.');
    await say('峯', false, 'other');
    const filed = await setStructure(env, { id: crypto.randomUUID(), structure: above }, form, 'reader', tools);
    expect(filed.structures.map((v: any) => [v.structure, [...v.by].sort()])).toEqual([[above, ['other', 'reader']]]);
    expect(beside).not.toBe(above);
  });

  it('names a structure by a regional shape of a character', async () => {
    const { env, tools, form, beside, above } = await forms();
    const by = (char: string, of: string) => setStructure(env, { id: crypto.randomUUID(), char, of }, form, 'reader', tools);
    expect((await by('⿱山夆', '嶺')).structures.map((v: any) => v.structure)).toEqual([beside]);
    expect((await by('⿱山領', '嶺')).structures.map((v: any) => v.structure)).toEqual([beside]);
    await expect(by('⿱山今', '嶺')).rejects.toThrow('422 ⿱山今 is no shape of 嶺.');
  });

  it('moves a form: the reader\'s claims of its other structures are taken back in the same save', async () => {
    const { db, env, tools, form, say, beside, above } = await forms();
    await say('峰');
    await say('峰', false, 'other');
    const id = crypto.randomUUID();
    const moved = await setStructure(env, { id, char: '峯', replace: true }, form, 'reader', tools);
    expect(moved.replaced).toEqual([beside]);
    expect(moved.structures.map((v: any) => [v.label, v.by])).toEqual([['⿰山夆', ['other']], ['⿱山夆', ['reader']]].sort((a, b) => a[0] < b[0] ? -1 : 1)
      .sort((a, b) => (a[0] === '⿰山夆' ? beside : above) < (b[0] === '⿰山夆' ? beside : above) ? -1 : 1));
    // The same save sent again answers alike and writes nothing.
    const actions = () => (db.query("SELECT count(*) AS n FROM assertion_actions WHERE action='retract'").get() as { n: number }).n;
    const before = actions();
    expect(await setStructure(env, { id, char: '峯', replace: true }, form, 'reader', tools)).toEqual(moved);
    expect(actions()).toBe(before);
    // Moving to where the form already stands takes back the rest alone.
    await say('峰');
    expect((await setStructure(env, { id: crypto.randomUUID(), char: '峯', replace: true }, form, 'reader', tools)).replaced).toEqual([beside]);
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='has_structure' AND asserted_by='reader' AND slot=?").get(above)).toEqual({ n: 1 });
  });

  it('files a written value nobody has chosen yet, naming its form with the claim', async () => {
    const { db, env, tools, beside, post } = await forms();
    const answer = await post(`/atlas/forms/${encodeURIComponent('⿰山夅')}/structure`, { id: crypto.randomUUID(), char: '峰' }) as any;
    expect(answer.structures).toEqual([{ structure: beside, label: '⿰山夆', by: ['reader'] }]);
    expect(db.query("SELECT r.scheme,r.value FROM forms f JOIN representations r ON r.id=f.anchor WHERE f.id=?").get(answer.id)).toEqual({ scheme: 'ids', value: '⿰山夅' });
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='represented_by' AND subject=?").get(answer.id)).toEqual({ n: 1 });
    // A character is a value too, and a second claim names the form no second time.
    const char = await setStructure(env, { id: crypto.randomUUID(), char: '峯' }, '峰', 'reader', tools);
    await setStructure(env, { id: crypto.randomUUID(), char: '峯' }, '峰', 'other', tools);
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='represented_by' AND subject=?").get(char.id)).toEqual({ n: 1 });
    await expect(setStructure(env, { id: crypto.randomUUID(), char: '峰' }, '⿰山', 'reader', tools)).rejects.toThrow('422 This description is missing a component.');
    await expect(setStructure(env, { id: crypto.randomUUID(), char: '峰', remove: true }, '⿱山夅', 'reader', tools))
      .rejects.toThrow('409 You have not said this form has this structure.');
    expect(db.query("SELECT count(*) AS n FROM representations WHERE value='⿱山夅'").get()).toEqual({ n: 0 });
  });

  it('reads the structures each written value is filed under, the one most readers name first', async () => {
    const { env, tools, say, beside, above } = await forms();
    expect(await claimedStructures(env, ['⿰山夆', '峰', '⿰山'])).toEqual(new Map());
    await say('峯');
    expect(await claimedStructures(env, ['峰', '⿰山夆'])).toEqual(new Map([['⿰山夆', [{ id: above, label: '⿱山夆', by: ['reader'] }]]]));
    await say('峰', false, 'other');
    await say('峰', false, 'third');
    expect((await claimedStructures(env, ['⿰山夆'])).get('⿰山夆')).toEqual([{ id: beside, label: '⿰山夆', by: expect.arrayContaining(['other', 'third']) },
      { id: above, label: '⿱山夆', by: ['reader'] }]);
    await setStructure(env, { id: crypto.randomUUID(), char: '峰', remove: true }, '⿰山夆', 'other', tools);
    // Among equals the lowest id stands first.
    expect((await claimedStructures(env, ['⿰山夆'])).get('⿰山夆')!.map(value => value.id)).toEqual([beside, above].sort());
    await setStructure(env, { id: crypto.randomUUID(), char: '峰', remove: true }, '⿰山夆', 'third', tools);
    expect((await claimedStructures(env, ['⿰山夆'])).get('⿰山夆')!.map(value => value.id)).toEqual([above]);
  });

  it('folds one structure into another, writes both rows, and takes the fold back', async () => {
    const { db, env, fold, beside, above } = await forms();
    const folded = await fold({ char: '峯' }, { char: '峰' });
    expect(folded).toEqual({ structure: above, folds_into: { id: beside, label: '⿰山夆', by: ['reader'] }, replaced: null });
    expect(db.query('SELECT id FROM structures ORDER BY id').all().map((row: any) => row.id)).toEqual([beside, above].sort());
    expect(db.query("SELECT subject,object,crop,method FROM assertions WHERE predicate='folds_into'").get())
      .toEqual({ subject: above, object: beside, crop: null, method: 'form-picker' });
    expect(await foldsOf(env, [above, beside])).toEqual(new Map([[above, { head: { id: beside, label: '⿰山夆' }, by: ['reader'], path: [beside] }]]));
    // The same fold made again is no new claim: what others said of the first stands.
    expect(await fold({ char: '峯' }, { structure: beside })).toEqual({ ...folded, replaced: null });
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='folds_into'").get()).toEqual({ n: 1 });
    expect(db.query("SELECT count(*) AS n FROM assertion_actions").get()).toEqual({ n: 0 });
    // Another reader joins; only one's own fold is theirs to take back.
    expect((await fold({ structure: above }, { structure: beside }, 'other')).folds_into?.by.sort()).toEqual(['other', 'reader']);
    expect(await fold({ structure: above }, null)).toEqual({ structure: above, folds_into: { id: beside, label: '⿰山夆', by: ['other'] }, replaced: beside });
    await expect(fold({ structure: above }, null)).rejects.toThrow('409 You have not folded this structure.');
    expect((await fold({ structure: above }, null, 'other')).folds_into).toBeNull();
    expect(await foldsOf(env, [above])).toEqual(new Map());
  });

  it('follows folds to their end, and a reader\'s new fold of a structure replaces their earlier one', async () => {
    const { env, fold, beside, above, wind } = await forms();
    await fold({ char: '峯' }, { char: '峰' });
    await fold({ char: '峰' }, { char: '嵐' });
    expect((await foldsOf(env, [above])).get(above)).toEqual({ head: { id: wind, label: '⿱山風' }, by: ['reader'], path: [beside, wind] });
    expect((await foldsOf(env, [beside])).get(beside)?.head.id).toBe(wind);
    const moved = await fold({ char: '峯' }, { char: '嵐' });
    expect(moved).toEqual({ structure: above, folds_into: { id: wind, label: '⿱山風', by: ['reader'] }, replaced: beside });
    // A regional shape names its character's structure, here 嶺's, though the same text alone is 峯's.
    expect((await fold({ char: '⿱山夆', of: '嶺' }, { char: '峯' })).structure).toBe(beside);
  });

  it('refuses a fold into itself, one that leads back, and a structure no claim has named', async () => {
    const { fold, beside, above, wind } = await forms();
    await expect(fold({ char: '峰' }, { char: '⿰山夅' })).rejects.toThrow('422 A structure folds into another.');
    await fold({ char: '峯' }, { char: '峰' });
    await fold({ char: '峰' }, { char: '嵐' });
    await expect(fold({ char: '嵐' }, { char: '峯' })).rejects.toThrow('409 That structure already stands under this one.');
    // Midway along the folds counts too: the new fold would take back the one that leads on from 峰.
    await expect(fold({ char: '峰' }, { char: '峯' })).rejects.toThrow('409 That structure already stands under this one.');
    await expect(fold({ structure: 'st:' + '1'.repeat(32) }, { structure: beside })).rejects.toThrow('422 No claim has named this structure.');
    await expect(fold({ structure: above }, { structure: 'st:' + '1'.repeat(32) })).rejects.toThrow('422 No claim has named this structure.');
    await expect(fold({ char: '丑' }, { structure: wind })).rejects.toThrow('422 No structure is known for 丑.');
    await expect(setFold((await forms()).env, { id: crypto.randomUUID(), from: 'x', into: { char: '峰' } }, 'reader', (await forms()).tools)).rejects.toThrow('422 Name a structure.');
  });

  it('folds nowhere while readers disagree, and stops a ring at its lowest id', async () => {
    const { db, env, fold, beside, above, wind } = await forms();
    await fold({ char: '峯' }, { char: '峰' });
    await fold({ char: '峯' }, { char: '嵐' }, 'other');
    expect(await foldsOf(env, [above])).toEqual(new Map());
    await fold({ structure: above }, null, 'other');
    expect((await foldsOf(env, [above])).get(above)?.head.id).toBe(beside);
    // A ring cannot be written through the route; rows that make one still answer.
    const at = '2026-11-16T00:00:00Z';
    db.query("INSERT INTO current_claims(subject,predicate,scope,slot,status,object,members,supporting,claims,resolver,at) VALUES(?,'folds_into','','','asserted',?,'[]','[]','[]','ledger-r1',?)")
      .run(beside, above, at);
    const ring = await foldsOf(env, [above, beside, wind]), low = [above, beside].sort()[0];
    expect([ring.get(above)?.head.id ?? above, ring.get(beside)?.head.id ?? beside]).toEqual([low, low]);
  });

  it('follows a chain longer than one read to the same end from every structure of it', async () => {
    const { db, env } = await forms();
    const chain = Array.from({ length: 21 }, (_, i) => 'st:' + i.toString(16).padStart(32, 'a'));
    const at = '2026-11-16T00:00:00Z';
    for (const id of chain) db.query("INSERT INTO structures(id,label,scheme,version,policy,created_by,created_at) VALUES(?,?,'babelstone-ids','v','structure-v1','u',?)").run(id, 'L' + id.slice(-2), at);
    for (let i = 0; i + 1 < chain.length; i++)
      db.query("INSERT INTO current_claims(subject,predicate,scope,slot,status,object,members,supporting,claims,resolver,at) VALUES(?,'folds_into','','','asserted',?,'[]','[]','[]','ledger-r1',?)")
        .run(chain[i], chain[i + 1], at);
    const folds = await foldsOf(env, [chain[0], chain[5], chain[13], chain[20]]);
    expect([chain[0], chain[5], chain[13]].map(id => folds.get(id)?.head)).toEqual(Array(3).fill({ id: chain[20], label: 'L' + chain[20].slice(-2) }));
    expect(folds.get(chain[0])!.path).toEqual(chain.slice(1));
    expect(folds.has(chain[20])).toBe(false);
  });

  it('a fold is written through the claim route and the same save sent again answers alike', async () => {
    const { db, post, beside, above } = await forms();
    const body = { id: crypto.randomUUID(), from: { char: '峯' }, into: { char: '峰' } };
    const answer = await post('/atlas/structures/folds', body);
    expect(answer).toEqual({ structure: above, folds_into: { id: beside, label: '⿰山夆', by: ['reader'] }, replaced: null });
    expect(await post('/atlas/structures/folds', body)).toEqual(answer);
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='folds_into'").get()).toEqual({ n: 1 });
  });

  it('is written through the claim route', async () => {
    const { env, form, post, beside } = await forms();
    const answer = await post(`/atlas/forms/${form}/structure`, { id: crypto.randomUUID(), char: '峰' }) as any;
    expect(answer.structures).toEqual([{ structure: beside, label: '⿰山夆', by: ['reader'] }]);
    expect(await structuresOf(env, form)).toEqual(answer.structures);
  });
});

describe('a batch of form picks', () => {
  it('claims one value on every crop at once, names its form once, and is undone by sending each replaced value back', async () => {
    const { db, env, tools, local, corpus, version, post } = setup();
    await setForm(env, { id: crypto.randomUUID(), crop_version: version(local), form: '還' }, publicId(local), 'reader', tools);
    const batch = { id: crypto.randomUUID(), crops: [{ id: publicId(local), crop_version: version(local), form: '⿺辶𦊷' },
      { id: publicId(corpus), crop_version: version(corpus, 'rev1'), form: '⿺辶𦊷' }] };
    const marked = await post('/atlas/forms', batch) as any;
    expect(marked.crops.map((c: any) => [c.id, c.form.values[0].text, c.replaced])).toEqual([[publicId(local), '⿺辶𦊷', '還'], [publicId(corpus), '⿺辶𦊷', null]]);
    expect(db.query('SELECT count(*) AS n FROM forms').get()).toEqual({ n: 2 });
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='represented_by'").get()).toEqual({ n: 2 });
    // The same batch sent again is the same write.
    expect((await post('/atlas/forms', batch) as any).crops).toEqual(marked.crops);
    expect(db.query("SELECT count(*) AS n FROM assertions WHERE predicate='has_form'").get()).toEqual({ n: 3 });
    const undone = await setForms(env, { id: crypto.randomUUID(), crops: marked.crops.map((c: any) => ({ id: c.id,
      crop_version: version(c.id === publicId(local) ? local : corpus, c.id === publicId(local) ? 'px' : 'rev1'), form: c.replaced })) }, 'reader', tools);
    expect(undone.crops.map((c: any) => [c.id, c.form?.values.map((v: any) => v.text) ?? null])).toEqual([[publicId(local), ['還']], [publicId(corpus), null]]);
  });

  it('is refused whole when a crop was cut again, naming it, or when a value is no form', async () => {
    const { db, env, tools, local, corpus, version } = setup();
    const send = (crops: unknown[]) => setForms(env, { id: crypto.randomUUID(), crops }, 'reader', tools);
    await expect(send([{ id: publicId(local), crop_version: version(local), form: '⿰木' }])).rejects.toThrow('422 This description is missing a component.');
    const stale = await send([{ id: publicId(local), crop_version: version(local), form: '還' }, { id: publicId(corpus), crop_version: version(corpus, 'old'), form: '還' }])
      .catch(error => error);
    expect(stale.message).toBe('409 Some of these crops were cut again. Reload them.');
    expect(stale.extra).toEqual({ targets: [{ id: publicId(corpus) }] });
    await expect(send([{ id: publicId(local), crop_version: version(local), form: '還' }, { id: publicId(local), crop_version: version(local), form: '還' }]))
      .rejects.toThrow('422 A batch names each crop once.');
    expect(db.query('SELECT count(*) AS n FROM assertions').get()).toEqual({ n: 0 });
    // Taking back a claim the reader never made leaves the crop out.
    expect((await send([{ id: publicId(local), crop_version: version(local), form: null }])).crops).toEqual([]);
  });
});

describe('a crop a claim names', () => {
  it('is read by public id, or by an old text id through the alias map; another text id asks for a reload', async () => {
    const { db, local, text, post, version } = setup();
    const body = { id: crypto.randomUUID(), crop_version: version(local), word: W, method: 'ruby', form: 'certain' };
    expect(await post(`/atlas/characters/${encodeURIComponent(text.local)}/word`, body)).toMatchObject({ id: publicId(local) });
    const stale = await post(`/atlas/characters/${encodeURIComponent(text.unmapped)}/word`, { ...body, id: crypto.randomUUID() }) as Response;
    expect(stale.status).toBe(409);
    expect(await stale.json()).toEqual({ detail: 'This page is from before an update. Reload it.', code: 'reload' });
    expect(db.query('SELECT count(*) AS n FROM assertions').get()).toEqual({ n: 1 });
  });

  it('as a candidate takes no claim', async () => {
    const { db, page, post, version } = setup();
    const candidate = cropId(page, 4);
    db.exec(`INSERT INTO crops(id,kind,book,state,quiz,shuffle) VALUES(${candidate},2,1,0,1,3)`);
    await expect(post(`/atlas/characters/${publicId(candidate)}/word`, { id: crypto.randomUUID(), crop_version: version(candidate), word: W, method: 'ruby', form: 'certain' }))
      .rejects.toThrow('404 This character is not in the published collection.');
  });

  it('retired in favour of another answers with that crop, as the old site did', async () => {
    const { retired, gone, local, post, version } = setup();
    const body = { id: crypto.randomUUID(), crop_version: version(local), word: W, method: 'ruby', form: 'certain' };
    const refused = await post(`/atlas/characters/${publicId(retired)}/word`, body).catch(e => e);
    expect([refused.message, refused.extra]).toEqual(['404 This crop was replaced.', { replaced_by: publicId(local) }]);
    await expect(post(`/atlas/characters/${publicId(gone)}/word`, body)).rejects.toThrow('404 This character is not in the published collection.');
    await expect(post('/atlas/characters/0/word', body)).rejects.toThrow('404 This character is not in the published collection.');
  });

  it('as a corpus glyph is recorded as written the first time, once', async () => {
    const { db, corpus, claim } = setup();
    db.exec('INSERT INTO corpus_rows VALUES(5,7)');
    const made = await claim([{ object: W, confidence: 1, confidence_scheme: 'form' }], 'reader', 'ruby', corpus);
    expect(made.subject).toBe(publicId(corpus));
    await claim([{ object: OTHER, confidence: 1, confidence_scheme: 'form' }], 'other', 'ruby', corpus);
    expect(db.query('SELECT crop,seq FROM corpus_rows ORDER BY seq').all()).toEqual([{ crop: 5, seq: 7 }, { crop: corpus, seq: 8 }]);
  });

  it('on an earlier cut no longer holds, and the slot is resolved without it', async () => {
    const { db, env, local, save } = setup();
    await save();
    db.exec(`UPDATE crops SET cut=1 WHERE id=${local}`);
    expect(await wordOfCrop(env, local)).toBeNull();
    const subject = (db.query('SELECT subject FROM assertions').get() as { subject: string }).subject;
    await env.ATLAS.batch(resolveSlots(env, [[subject, 'writes_word', '', '']]));
    expect(db.query('SELECT count(*) AS n FROM current_claims').get()).toEqual({ n: 0 });
    db.exec("UPDATE assertions SET cut=1; UPDATE assertion_evidence SET cut=1");
    await env.ATLAS.batch(resolveSlots(env, [[subject, 'writes_word', '', '']]));
    expect(db.query('SELECT crop,cut,status FROM current_claims').get()).toEqual({ crop: local, cut: 1, status: 'asserted' });
  });
});

describe('a saved submission', () => {
  it('from before the cutover answers again, its crop read from its text id', async () => {
    const { db, env, tools, local, text, input, post } = setup();
    const first = input(), named = publicId(local);
    db.query('INSERT INTO ledger_submissions VALUES(?,?,?,?,?)').run('reader:' + first.id, 'reader', JSON.stringify({ crop: named, input: first }),
      JSON.stringify({ submission: 'reader:' + first.id, subject: text.local, replaced: null, retracted: [] }), 't');
    expect(await setWord(env, first, named, 'reader', tools)).toEqual({ id: named, word: null, replaced: null });
    const body = { id: crypto.randomUUID(), subject: named, predicate: 'writes_word', claims: [] };
    db.query('INSERT INTO ledger_submissions VALUES(?,?,?,?,?)').run('reader:' + body.id, 'reader', JSON.stringify(body),
      JSON.stringify({ submission: 'reader:' + body.id, subject: text.local, crop: local, cut: 0, assertions: ['cf:x'], retracted: [] }), 't');
    expect(await post('/atlas/claims', body)).toEqual({ submission: 'reader:' + body.id, subject: named, assertions: ['cf:x'], retracted: [], current: [] });
    const other = { id: crypto.randomUUID(), action: 'accept' };
    db.query('INSERT INTO ledger_submissions VALUES(?,?,?,?,?)').run('reader:' + other.id, 'reader', JSON.stringify({ target: 'cf:d', input: other }),
      JSON.stringify({ submission: 'reader:' + other.id, subject: 'book:x', actions: ['cf:y'], retracted: [] }), 't');
    expect(await post('/atlas/claims/cf:d/actions', other)).toEqual({ submission: 'reader:' + other.id, subject: 'book:x', actions: ['cf:y'], retracted: [], current: [] });
  });
});

describe('a pending redraw', () => {
  const pending = (db: Database, crop: number) => db.exec(`INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${crop},0,0,0,1,2,3,4)`);
  it('moves a crop to its next cut at once: claims made meanwhile hold through the publication', async () => {
    const { db, env, local, save } = setup();
    await save({}, 'before');
    pending(db, local);
    expect(await wordOfCrop(env, local)).toBeNull();
    const redrawn = `${publicId(local)}@px@1,2,3,4`;
    await expect(save({ crop_version: `${publicId(local)}@px@0,0,10,10` })).rejects.toThrow('409 This crop was cut again. Reload it.');
    expect((await save({ crop_version: redrawn, word: OTHER })).word).toMatchObject({ status: 'asserted', values: [{ word: OTHER }], by: ['reader'] });
    expect(db.query("SELECT DISTINCT cut FROM assertion_evidence WHERE kind='crop' AND assertion IN (SELECT id FROM assertions WHERE asserted_by='reader')").all()).toEqual([{ cut: 1 }]);
    // The publication cuts the redrawn box: the crop takes cut 1 and the pending row goes.
    db.exec(`UPDATE crops SET cut=1 WHERE id=${local}; DELETE FROM reviews WHERE crop=${local}`);
    expect((await wordOfCrop(env, local))?.values).toMatchObject([{ word: OTHER }]);
  });

  it('undone, returns the crop to its published cut and the claims made on it', async () => {
    const { db, env, local, save } = setup();
    await save({}, 'before');
    pending(db, local);
    await save({ crop_version: `${publicId(local)}@px@1,2,3,4`, word: OTHER });
    db.exec(`UPDATE reviews SET x=NULL,y=NULL,w=NULL,h=NULL WHERE crop=${local}`);
    const subject = (db.query('SELECT subject FROM assertions LIMIT 1').get() as { subject: string }).subject;
    await env.ATLAS.batch(resolveSlots(env, [[subject, 'writes_word', '', '']]));
    expect(await wordOfCrop(env, local)).toMatchObject({ values: [{ word: W }], by: ['before'] });
  });
});

describe('the ledger read', () => {
  it('names a crop by public id, moves an old text id there, and reads a book by its key', async () => {
    const { db, local, text, claim, read, post, reads } = setup();
    const made = await claim([{ object: W, confidence: 1, confidence_scheme: 'form' }], 'reader', 'ruby'), claimed = made.assertions[0];
    expect(typeof claimed).toBe('string');
    expect(made).toMatchObject({ subject: publicId(local), assertions: [expect.any(String)], retracted: [],
      current: [{ subject: publicId(local), crop: publicId(local), cut: 0, predicate: 'writes_word', status: 'asserted' }] });
    const accepted = await post(`/atlas/claims/${claimed}/actions`, { id: crypto.randomUUID(), action: 'accept' }, { id: 'other', admin: false }) as any;
    expect(accepted).toMatchObject({ subject: publicId(local), actions: [expect.any(String)], current: [{ status: 'accepted' }] });
    const found = await read(`/atlas/claims?subject=${publicId(local)}`) as any;
    expect(found.subject).toBe(publicId(local));
    expect(found.history).toMatchObject([{ id: claimed, subject: publicId(local), crop: publicId(local), cut: 0,
      evidence: [{ kind: 'crop', ref: `${publicId(local)}@px@0,0,10,10`, crop: publicId(local), cut: 0, locator: null }], actions: [{ action: 'accept' }] }]);
    const moved = await read(`/atlas/claims?subject=${encodeURIComponent(text.local)}`) as Response;
    expect([moved.status, moved.headers.get('location')]).toEqual([302, `https://x/atlas/claims?subject=${publicId(local)}`]);
    db.exec(`INSERT INTO assertions(id,subject,predicate,value,tier,asserted_by,asserted_at) VALUES('cf:d','book:x','date_produced','{"text":"1700"}','attested','export','t')`);
    db.exec("INSERT INTO books(id,key) VALUES(2,'book:x')");
    reads.length = 0;
    expect(await read('/atlas/claims?subject=book:x')).toMatchObject({ subject: 'book:x', current: [], history: [{ id: 'cf:d', crop: null }] });
    expect(await read('/atlas/claims?subject=fm:1')).toMatchObject({ subject: 'fm:1', history: [] });
    expect(await read(`/atlas/claims?subject=${encodeURIComponent(W)}`)).toMatchObject({ subject: W, history: [] });
    // A book, form or word never reads the alias map.
    expect(reads).toEqual([]);
    const exported = await read('/atlas/ledger.json?limit=1') as Response;
    expect(exported.headers.get('content-disposition')).toBe('attachment; filename="atlas-ledger.json"');
    expect(await exported.json()).toMatchObject({ kind: 'atlas-ledger', assertions: [{ seq: 1, subject: publicId(local), crop: publicId(local) }],
      actions: [{ seq: 1, action: 'accept' }], next: { after: 1, actions_after: 1 }, done: false });
  });

  it('gives a book its dates and the date claims that stand', async () => {
    const { db, env } = setup();
    db.exec(`INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source)
        VALUES(1,'witness','copied',1700,1700,'year','元禄十三年','1700','single','japanese','src:1');
      INSERT INTO assertions(id,subject,predicate,value,tier,asserted_by,asserted_at) VALUES
        ('cf:d1',(SELECT key FROM books WHERE id=1),'date_copied','{"text":"元禄十三年","start":1700}','attested','export','t'),
        ('cf:d2',(SELECT key FROM books WHERE id=1),'date_other','{"text":"x"}','attested','export','t');
      INSERT INTO assertion_evidence(assertion,kind,ref,locator) VALUES('cf:d1','source','src:1','f. 3');
      INSERT INTO assertion_actions(id,assertion,action,actor,at) VALUES('x','cf:d2','retract','export','t');`);
    expect(await bookDates(env, 1)).toMatchObject({ dating: { witness: { kind: 'copied', start: 1700, text: '元禄十三年' } },
      dates: [{ id: 'cf:d1', kind: 'copied', text: '元禄十三年', start: 1700, source: 'src:1', locator: 'f. 3', tier: 'attested' }] });
    expect(await bookDates(env, null)).toEqual({ dating: {} });
  });
});

describe('the ledger queries', () => {
  it('read by key or index and scan no big table', () => {
    const db = new Database(':memory:'); databases.push(db);
    atlasSchema(db);
    const plans: [string, unknown[]][] = [
      [cropNowQuery(), [1]], [cropSlotsQuery('has_form'), ['[1]']], [cropSlotsQuery('writes_word'), ['[1]']], [formNamesQuery(), ['["fm:1"]']],
      [wordNamesQuery(), ['["w"]']], [wordMethodsQuery(), ['["a"]']], [ownClaimQuery('has_form', '(SELECT key FROM actors WHERE user=\'u\')'), [1, 'u']],
      [ownClaimQuery('writes_word', '(SELECT key FROM actors WHERE user=\'u\')'), [1, 'u']], [currentCropClaimsQuery(), [1]], [currentClaimsQuery(), ['s']],
      [claimHistoryQuery('crop'), [1]], [claimHistoryQuery('subject'), ['s']], [ledgerClaimsQuery(), [0, 10]], [ledgerActionsQuery(), [0, 10]],
      [bookDatesQuery(), [1]], [bookKeyQuery(), ['k']], [bookDateClaimsQuery(), [1]], [resolveClearQuery(), ['[["s","p","",""]]']], [corpusRowQuery(), [1]],
      [structuresQuery(), ['fm:1']], [structureLabelsQuery(), ['["st:1"]']], [ownStructuresQuery('(SELECT key FROM actors WHERE user=\'u\')'), ['fm:1', 'u']],
      [claimedStructuresQuery(), ['["fm:1"]']], [foldsQuery(), ['["st:1"]']], [ownFoldQuery('(SELECT key FROM actors WHERE user=\'u\')'), ['st:1', 'u']],
    ];
    for (const [sql, args] of plans) {
      const detail = (db.query('EXPLAIN QUERY PLAN ' + sql).all(...(args as any[])) as { detail: string }[]).map(r => r.detail);
      expect([sql, detail.filter(d => /^SCAN (a|c|k|e|x|f|r|p|crops|assertions|assertion_actions|assertion_evidence|current_claims|forms|representations|structures|s|words|book_dates|pages|reviews)\b/.test(d))])
        .toEqual([sql, []]);
    }
  });
});
