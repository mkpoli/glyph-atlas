import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { cropWordsQuery, setWord, withWords, wordChoices, wordChoicesQuery, wordMethodsQuery, wordNamesQuery, wordSpellingsQuery, wordsFor } from './cropWords';
import { resolveSlots, writeClaim, type LedgerTools } from './ledger';

const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });
const W = 'ja/ばかり/副助詞', OTHER = 'ja/のみ/副助詞';
function setup() {
  const db = new Database(':memory:'); databases.push(db);
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  db.exec(`INSERT INTO words VALUES('${W}','ja','ばかり','副助詞'),('${OTHER}','ja','のみ','副助詞');
    INSERT INTO units(id,origin,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
    VALUES('crop','local','handwritten','kanji','pending',0,0,0,0,'{"image_sha256":"pixels"}','{}','{}','{}');`);
  const env = { DB: d1(db) } as unknown as Env;
  const tools: LedgerTools = {
    fail: (status, message): never => { throw new Error(`${status} ${message}`); },
    body: async request => await request.json(), canonical: JSON.stringify,
    text: value => typeof value === 'string' ? value : null,
    owned: actor => actor === 'reader' ? "('old-reader')" : "('nobody')",
    crop: async (_, id) => db.query('SELECT id,crop_version AS version FROM units WHERE id=?').get(id) as { id: string; version: string | null },
  };
  const input = (value = {}) => ({ id: crypto.randomUUID(), crop_version: 'crop@pixels@', word: W, method: 'ruby', form: 'certain', ...value });
  const save = (value = {}, actor = 'reader') => setWord(env, input(value), 'crop', actor, tools);
  const claim = (subject: string, members: any[], actor: string, method = 'context') => writeClaim(env,
    { id: crypto.randomUUID(), crop_version: subject + '@pixels@', subject, predicate: 'writes_word', method, claims: members }, actor, tools);
  const spelling = (word: string, char: string, source = 'dictionary') => db.query(`INSERT INTO word_spellings
    VALUES(?,?,?,'entry','attested','source','','','',NULL,NULL)`).run(word, char, source);
  return { db, env, tools, input, save, claim, spelling };
}

describe('the crop word picker', () => {
  it('saves once, replaces its own word, and clears only its own claim', async () => {
    const { db, env, tools, input, save } = setup();
    const first = input(), answer = await setWord(env, first, 'crop', 'reader', tools);
    expect(answer.replaced).toBeNull();
    expect(answer.word).toMatchObject({ status: 'asserted', values: [{ word: W, reading: 'ばかり', class: '副助詞', form: 'certain' }], methods: ['ruby'], by: ['reader'] });
    expect(await setWord(env, first, 'crop', 'reader', tools)).toEqual(answer);
    expect(db.query('SELECT count(*) AS n FROM assertions').get()).toEqual({ n: 1 });
    await expect(setWord(env, { ...first, word: OTHER }, 'crop', 'reader', tools)).rejects.toThrow('409');
    const replaced = await save({ word: OTHER, form: 'probable', method: 'context' });
    expect(replaced.replaced).toEqual({ word: W, method: 'ruby', form: 'certain' });
    expect(replaced.word?.values).toEqual([{ word: OTHER, reading: 'のみ', class: '副助詞', form: 'probable' }]);
    await save({}, 'other');
    const cleared = await save({ word: null });
    expect(cleared.replaced).toEqual({ word: OTHER, method: 'context', form: 'probable' });
    expect(cleared.word?.by).toEqual(['other']);
    expect(cleared.word?.values[0].word).toBe(W);
    await expect(save({ word: null })).rejects.toThrow('409 You have no word on this crop to clear.');
    const clear = input({ word: null });
    const last = await setWord(env, clear, 'crop', 'other', tools);
    expect(last.word).toBeNull();
    expect(await setWord(env, clear, 'crop', 'other', tools)).toEqual(last);
    expect(db.query('SELECT revision,state FROM units').get()).toEqual({ revision: 0, state: 'pending' });
  });

  it('recognises an earlier actor id belonging to the same reader', async () => {
    const { save } = setup();
    await save({}, 'old-reader');
    const result = await save({ word: null });
    expect(result.word).toBeNull();
    expect(result.replaced?.word).toBe(W);
  });

  it('refuses unknown words, missing evidence, invalid confidence and stale cuts before writing', async () => {
    const { db, save } = setup();
    for (const word of ['missing', {}, undefined]) await expect(save({ word })).rejects.toThrow('422 No such word.');
    for (const method of [undefined, '', 'guess']) await expect(save({ method })).rejects.toThrow('422');
    for (const form of [undefined, 'maybe', 1]) await expect(save({ form })).rejects.toThrow('422');
    await expect(save({ crop_version: 'crop@old@' })).rejects.toThrow('409 This crop was cut again. Reload it.');
    expect(db.query('SELECT count(*) AS n FROM ledger_submissions').get()).toEqual({ n: 0 });
    await save();
    await expect(save({ word: null, crop_version: 'crop@old@' })).rejects.toThrow('409 This crop was cut again. Reload it.');
    db.exec("UPDATE units SET data='{}'");
    await expect(save()).rejects.toThrow('409 This crop has no image to make a claim about.');
  });

  it('reads glosses, alternatives, disputed standing values and the strongest supporting methods', async () => {
    const { db, env, save, claim } = setup();
    await claim('crop', [{ value: { gloss: 'only this much' }, confidence: 0.5, confidence_scheme: 'form' }], 'glosser');
    expect((await wordsFor(env, ['crop'])).get('crop')?.values).toEqual([{ gloss: 'only this much', form: 'probable' }]);
    await save({ word: null }, 'glosser');
    await save();
    const disputed = await save({ word: OTHER, form: 'probable', method: 'note' }, 'other');
    expect(disputed.word).toMatchObject({ status: 'disputed', values: [
      { word: W, reading: 'ばかり', class: '副助詞', form: 'certain' }, { word: OTHER, reading: 'のみ', class: '副助詞', form: 'probable' },
    ], supporting: [], methods: [], by: ['reader', 'other'] });
    await save({ word: null }, 'other');
    await save({ method: 'context' }, 'context');
    await save({ method: 'gloss' }, 'gloss');
    await save({ method: 'ruby' }, 'ruby');
    expect((await wordsFor(env, ['crop'])).get('crop')?.methods).toEqual(['gloss', 'ruby', 'context']);
    // A rejected competing claim contributes neither a value nor a method nor an asserter.
    const different = await claim('crop', [{ object: OTHER, confidence: 1, confidence_scheme: 'form' }], 'rejected', 'note');
    db.query("INSERT INTO assertion_actions(id,assertion,action,actor,at) VALUES('reject',?,'reject','voter','now')").run(different.assertions[0]);
    await env.DB.batch(resolveSlots(env, [['crop', 'writes_word', '', '']]));
    const current = (await wordsFor(env, ['crop'])).get('crop')!;
    expect(current.values.map((v: any) => v.word)).toEqual([W]);
    expect(current.by).not.toContain('rejected');
    expect(current.methods).not.toContain('note');
    for (const actor of ['reader', 'context', 'gloss', 'ruby']) await save({ word: null }, actor);
    await claim('crop', [W, OTHER].map(object => ({ object, confidence: 1, confidence_scheme: 'form' })), 'alternatives');
    expect((await wordsFor(env, ['crop'])).get('crop')?.values).toHaveLength(2);
    await save({ word: null }, 'alternatives');
    expect((await wordsFor(env, ['crop'])).get('crop')?.values).toEqual([]);
  });

  it('leaves out an older crop version and bounds pages of crops', async () => {
    const { db, env, save } = setup();
    await save();
    db.exec("UPDATE current_claims SET crop_version='crop@old@'");
    expect((await wordsFor(env, ['crop'])).size).toBe(0);
    expect(await withWords(env, [{ id: 'crop' }, { id: 'unknown' }])).toEqual([{ id: 'crop', word: null }, { id: 'unknown', word: null }]);
    db.exec("UPDATE current_claims SET crop_version='crop@pixels@'");
    expect((await wordsFor(env, Array(205).fill('crop'))).size).toBe(1);
    expect((await wordsFor(env, [...Array.from({ length: 200 }, (_, i) => 'absent' + i), 'crop'])).size).toBe(0);
  });

  it('reports no form confidence from a different scheme', async () => {
    const { db, env, save } = setup();
    await save();
    db.exec(`UPDATE current_claims SET members=json_set(members,'$[0].scheme','other')`);
    expect((await wordsFor(env, ['crop'])).get('crop')?.values[0].form).toBeNull();
  });
});

describe('word choices', () => {
  it('offers the crop spellings first, folds katakana, and lists distinct spellings', async () => {
    const { env, spelling } = setup();
    spelling(W, '計'); spelling(W, '計', 'other'); spelling(W, '許'); spelling(OTHER, '而已');
    expect(await wordChoices(env, 'ノ', ['計'])).toEqual([
      { id: W, reading: 'ばかり', class: '副助詞', spellings: ['計', '許'] },
      { id: OTHER, reading: 'のみ', class: '副助詞', spellings: ['而已'] },
    ]);
    expect((await wordChoices(env, 'バ', ['許', '許'])).map(r => r.id)).toEqual([W]);
    expect(await wordChoices(env, '', [])).toEqual([]);
    expect(await wordChoices(env, '%', [])).toEqual([]);
    expect(await wordChoices(env, '\u{10ffff}', [])).toEqual([]);
    expect(await wordChoices(env, '', ['a', 'b', 'c', 'd', '計'])).toEqual([]);
  });

  it('limits choices to twenty, keeping words spelled by the crop first', async () => {
    const { db, env, spelling } = setup();
    spelling(W, '計');
    for (let i = 0; i < 25; i++) db.query("INSERT INTO words VALUES(?,'ja',?,'名詞')").run('ja/あ' + i + '/名詞', 'あ' + i);
    const found = await wordChoices(env, 'ア', ['計']);
    expect(found).toHaveLength(20);
    expect(found[0].id).toBe(W);
    expect(found.slice(1).every(r => r.reading.startsWith('あ'))).toBe(true);
  });

  it('uses keys and the spelling index without scanning corpus or vocabulary tables', () => {
    const { db } = setup();
    const plans = [
      [cropWordsQuery(), [JSON.stringify(['crop'])]], [wordNamesQuery(), [JSON.stringify([W])]],
      [wordMethodsQuery(), [JSON.stringify(['assertion'])]], [wordChoicesQuery(), [JSON.stringify(['計'])]],
      [wordChoicesQuery(true), ['ja/ば', 'ja/ぱ']], [wordSpellingsQuery(), [JSON.stringify([W])]],
    ] as [string, string[]][];
    const details = plans.map(([sql, args]) => (db.query('EXPLAIN QUERY PLAN ' + sql).all(...args) as { detail: string }[]).map(r => r.detail));
    for (const plan of details) expect(plan.filter(d => /^SCAN (c|u|units|current_claims|words|word_spellings|assertions)\b/.test(d))).toEqual([]);
    expect(details[0].some(d => /SEARCH c USING PRIMARY KEY/.test(d))).toBe(true);
    expect(details[0].some(d => /SEARCH u USING INDEX/.test(d))).toBe(true);
    expect(details[3].some(d => /SEARCH word_spellings USING COVERING INDEX word_spelling_spelling/.test(d))).toBe(true);
    expect(details[4]).toContain('SEARCH words USING PRIMARY KEY (id>? AND id<?)');
  });
});
