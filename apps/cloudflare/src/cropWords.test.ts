import { afterEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { cropWordsQuery, withWords, wordMethodsQuery, wordNamesQuery, wordsFor } from './cropWords';

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
  const spelling = (word: string, char: string, source = 'dictionary') => db.query(`INSERT INTO word_spellings
    VALUES(?,?,?,'entry','attested','source','','','',NULL,NULL)`).run(word, char, source);
  return { db, env, spelling };
}


// The picker and the slot's shape are tested on glyph-atlas-v3 in routes/claims.test.ts; here, the
// listings that still read the old database find a crop's slot on its current version only.
describe('a crop word in the old database', () => {
  it('leaves out an older crop version and bounds pages of crops', async () => {
    const { db, env } = setup();
    db.exec(`INSERT INTO assertions(id,subject,predicate,object,tier,asserted_by,asserted_at,confidence,confidence_scheme,method)
        VALUES('cf:1','crop','writes_word','${W}','observed','reader','t',1,'form','ruby');
      INSERT INTO current_claims(subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop_version,resolver,at)
        VALUES('crop','writes_word','','','asserted','${W}',NULL,
          '[{"assertion":"cf:1","object":"${W}","value":null,"confidence":1,"scheme":"form"}]','["cf:1"]',
          '[{"claim":"cf:1","asserted_by":"reader","standing":true,"members":[{"assertion":"cf:1","object":"${W}"}]}]','crop@old@','ledger-r1','t');`);
    expect((await wordsFor(env, ['crop'])).size).toBe(0);
    expect(await withWords(env, [{ id: 'crop' }, { id: 'unknown' }])).toEqual([{ id: 'crop', word: null }, { id: 'unknown', word: null }]);
    db.exec("UPDATE current_claims SET crop_version='crop@pixels@'");
    expect((await wordsFor(env, ['crop'])).get('crop')).toEqual({ status: 'asserted', values: [{ word: W, reading: 'ばかり', class: '副助詞', form: 'certain' }],
      methods: ['ruby'], supporting: ['cf:1'], by: ['reader'] });
    expect((await wordsFor(env, Array(205).fill('crop'))).size).toBe(1);
    expect((await wordsFor(env, [...Array.from({ length: 200 }, (_, i) => 'absent' + i), 'crop'])).size).toBe(0);
  });
});

describe('crop word reads', () => {
  it('use keys without scanning corpus or vocabulary tables', () => {
    const { db } = setup();
    const plans = [
      [cropWordsQuery(), [JSON.stringify(['crop'])]], [wordNamesQuery(), [JSON.stringify([W])]],
      [wordMethodsQuery(), [JSON.stringify(['assertion'])]],
    ] as [string, string[]][];
    const details = plans.map(([sql, args]) => (db.query('EXPLAIN QUERY PLAN ' + sql).all(...args) as { detail: string }[]).map(r => r.detail));
    for (const plan of details) expect(plan.filter(d => /^SCAN (c|u|units|current_claims|words|word_spellings|assertions)\b/.test(d))).toEqual([]);
    expect(details[0].some(d => /SEARCH c USING PRIMARY KEY/.test(d))).toBe(true);
    expect(details[0].some(d => /SEARCH u USING INDEX/.test(d))).toBe(true);
  });
});
