import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { senses, senseWordsQuery, sensesQuery, type SenseRow, type WordRow } from './senses';

function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}

describe("a character's attested senses", () => {
  const db = migrated();
  const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document)
    VALUES(?,'local',?,'handwritten','kanji','checked',0,1,1,1,?,'{}','{}','{}',?)`);
  const claim = db.prepare(`INSERT INTO current_claims(subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop_version,resolver,at)
    VALUES(?,'writes_word','','',?,?,NULL,?,?,'[]',?,'ledger-r1','2026-10-17')`);
  const assertion = db.prepare(`INSERT INTO assertions(id,subject,predicate,object,tier,asserted_by,asserted_at,confidence,confidence_scheme,method)
    VALUES(?,?,'writes_word',?,'observed','ann','2026-10-17',?,'form',?)`);
  // A crop's version is its id and image checksum with no box (0047): `{id}@h@`.
  const attest = (id: string, char: string, document: string, status: string, confidence: number, method: string, version = id + '@h@') => {
    unit.run(id, char, JSON.stringify({ id, label: char, image_sha256: 'h', source: document + 'の本（文庫）', holder: '文庫', page_number: 3, text: '西夷地の' + char }), document);
    assertion.run('a:' + id, id, 'ja/にしん/名詞', confidence, method);
    claim.run(id, status, 'ja/にしん/名詞', JSON.stringify([{ assertion: 'a:' + id, object: 'ja/にしん/名詞', value: null, confidence, scheme: 'form' }]),
      JSON.stringify(['a:' + id]), version);
  };
  attest('c1', '𩹉', 'd1', 'asserted', 1, 'gloss');
  attest('c2', '𩹉', 'd2', 'accepted', 0.5, 'context');
  attest('c3', '𩹉', 'd2', 'rejected', 1, 'context');
  attest('c4', '𩹉', 'd3', 'asserted', 1, 'ruby', 'c4@old');   // resolved on an earlier cut
  attest('c5', '鯡', 'd1', 'asserted', 1, 'ruby');
  db.exec("INSERT INTO words VALUES('ja/にしん/名詞','ja','にしん','名詞')");
  db.exec("INSERT INTO word_spellings VALUES('ja/にしん/名詞','鯡','wiktionary-ja','ニシン, revision 1927940','attested','source','','','{{ja-noun|鰊|鯡}}',NULL,NULL)");

  it('reads standing claims on current crops of the character, by the object index', () => {
    const rows = db.query(sensesQuery()).all('𩹉') as SenseRow[];
    expect(rows.map(r => r.subject)).toEqual(['c1', 'c2']);
    const plan = (db.query('EXPLAIN QUERY PLAN ' + sensesQuery()).all('𩹉') as { detail: string }[]).map(r => r.detail);
    expect(plan.some(d => /current_claim_object/.test(d))).toBe(true);
    expect(plan.some(d => /^SCAN (u|units|a|assertions)\b/.test(d))).toBe(false);
  });

  it('groups them by word, with the spellings its sources state, the documents and the form judgment', () => {
    const rows = db.query(sensesQuery()).all('𩹉') as SenseRow[];
    const words = db.query(senseWordsQuery()).all(JSON.stringify(['ja/にしん/名詞'])) as WordRow[];
    const [sense] = senses(rows, words);
    expect([sense.id, sense.reading, sense.crops, sense.documents]).toEqual(['ja/にしん/名詞', 'にしん', 2, 2]);
    expect(sense.spellings).toEqual([{ spelling: '鯡', code_point: 'U+9BE1', sources: ['wiktionary-ja'] }]);
    expect(sense.witnesses.map(w => [w.id, w.method, w.form, w.status, w.source, w.text])).toEqual([
      ['c1', 'gloss', 'certain', 'asserted', 'd1の本（文庫）', '西夷地の𩹉'],
      ['c2', 'context', 'probable', 'accepted', 'd2の本（文庫）', '西夷地の𩹉']]);
  });
});
