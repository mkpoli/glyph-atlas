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
  formOfCrop, graphemeHeadQuery, ownClaimQuery, setForm, setGrapheme, setWord, wordMethodsQuery, wordNamesQuery, wordOfCrop } from './claims';
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
const atlasSchema = (db: Database) => {
  const folder = new URL('../../migrations-atlas/', import.meta.url);
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
  const env = { ATLAS: d1(db), MEDIA } as unknown as Env;
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
  return { db, env, tools, text, local, corpus, retired, gone, version, input, save, post, claim, read, reads, page };
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

  it('names only a grapheme that heads its family, on the generic route too', async () => {
    const { db, local, version, pick, post } = graphemes();
    await expect(pick('U+4ECE')).rejects.toThrow('422 This character is filed under 從.');
    await expect(pick('U+9999')).rejects.toThrow('422 No such character.');
    await expect(pick('後')).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(pick('U+005F8C')).rejects.toThrow('422 Name a grapheme by its code points.');
    for (const key of ['U+110000', 'U+D800', 'U+FFFFFF']) await expect(pick(key)).rejects.toThrow('422 Name a grapheme by its code points.');
    await expect(pick(null)).rejects.toThrow('409 You have no grapheme on this crop to clear.');
    const claim = (object: string) => post('/atlas/claims', { id: crypto.randomUUID(), crop_version: version(local), subject: publicId(local),
      predicate: 'has_grapheme', claims: [{ object }] }) as Promise<any>;
    await expect(claim('U+4ECE')).rejects.toThrow('422 U+4ECE cannot be the object of has_grapheme.');
    await expect(claim('U+005F8C')).rejects.toThrow('422 U+005F8C cannot be the object of has_grapheme.');
    await expect(claim('U+110000')).rejects.toThrow('422 U+110000 cannot be the object of has_grapheme.');
    expect((await claim(AFTER)).current[0]).toMatchObject({ predicate: 'has_grapheme', status: 'asserted', object: AFTER });
    expect(db.query(graphemeHeadQuery()).get('从')).toEqual({ id: 4, head: 0, family: '從' });
  });

  it('files the crop under the family its grapheme is filed under after a publication moves it', async () => {
    const { db, env, local, pick } = graphemes();
    await pick(AFTER);
    // 後 joins 從's family: the crop read as 後 is listed with 從, and still not with 后.
    db.exec('UPDATE graphemes SET head=3 WHERE id=1');
    expect((await familyClaims(env, 'U+5F9E', [3, 4, 1])).here.map(r => r.id)).toEqual([local]);
    expect((await familyClaims(env, QUEEN, [2])).away.map(r => r.id)).toEqual([local]);
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
    ];
    for (const [sql, args] of plans) {
      const detail = (db.query('EXPLAIN QUERY PLAN ' + sql).all(...(args as any[])) as { detail: string }[]).map(r => r.detail);
      expect([sql, detail.filter(d => /^SCAN (a|c|k|e|x|f|r|p|crops|assertions|assertion_actions|assertion_evidence|current_claims|forms|representations|words|book_dates|pages|reviews)\b/.test(d))])
        .toEqual([sql, []]);
    }
  });
});
