// The senses a character's crops are attested writing (`writes_word`, data/ledger.json; issue #679):
// each word a standing claim names for a crop filed under the character, with the crops that attest
// it, their documents, how each sense is known and how sure its reader is of the form. A dictionary
// states what a character can mean; these are what the pages show it meaning.
import { withDating } from './dating';

type Json = Record<string, any>;
// Claims are found by the object index of `current_claims` (0048) and their crops by key, so a read
// grows with the ledger's word claims, not with the character's crops. A claim resolved on an earlier
// cut of its crop does not count, and neither does a rejected one. A gloss claim names no object and
// has no index to be found by; it shows on its crop's own record.
export const SENSES_MAX = 500;
export const sensesQuery = () => `SELECT c.subject,c.object,c.status,c.members,a.method,u.document,u.data
  FROM current_claims c INDEXED BY current_claim_object JOIN units u ON u.id=c.subject AND u.crop_version IS c.crop_version
  JOIN assertions a ON a.id=json_extract(c.supporting,'$[0]')
  WHERE c.predicate='writes_word' AND c.object IS NOT NULL AND c.status<>'rejected' AND u.character=?
  ORDER BY c.object,c.subject LIMIT ${SENSES_MAX}`;
// The words the claims name, with the spellings their sources state.
export const senseWordsQuery = () => `SELECT w.id,w.reading,w.class,s.spelling,s.source FROM words w
  LEFT JOIN word_spellings s ON s.word=w.id WHERE w.id IN (SELECT value FROM json_each(?)) ORDER BY w.id,s.spelling`;

export type SenseRow = { subject: string; object: string; status: string; members: string; method: string | null; document: string | null; data: string };
export type WordRow = { id: string; reading: string; class: string; spelling: string | null; source: string | null };
// A form judged in the `form` scheme: 1 certain, 0.5 probable (data/ledger.json).
const FORM: Record<number, string> = { 1: 'certain', 0.5: 'probable' };
// A one-character spelling's code point, which its character page is addressed by.
const pointOf = (spelling: string) => [...spelling].length === 1
  ? 'U+' + spelling.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0') : null;

/** The senses, one per word, its witnesses in document order: each crop with its document, how the
 *  sense is known, the form judgment and the slot's status. */
export function senses(rows: SenseRow[], words: WordRow[]) {
  const named = new Map<string, { id: string; reading: string; class: string; spellings: Map<string, string[]> }>();
  for (const w of words) {
    const word = named.get(w.id) ?? { id: w.id, reading: w.reading, class: w.class, spellings: new Map() };
    if (w.spelling) word.spellings.set(w.spelling, [...(word.spellings.get(w.spelling) ?? []), w.source!]);
    named.set(w.id, word);
  }
  const by = new Map<string, Json[]>();
  for (const row of rows) {
    const d = JSON.parse(row.data), member = (JSON.parse(row.members) as Json[])[0] ?? {};
    // `source` names the document as the gallery credits it (title and holder); `text` is the line as transcribed.
    const witness = { id: row.subject, image: d.image ?? null, source: d.source ?? null, holder: d.holder ?? null,
      document: row.document, page: d.page_number ?? null, text: d.text ?? null, status: row.status, method: row.method,
      form: member.scheme === 'form' ? FORM[member.confidence] ?? null : null };
    by.set(row.object, [...(by.get(row.object) ?? []), witness]);
  }
  return [...by].map(([id, witnesses]) => {
    const word = named.get(id);
    return { id, reading: word?.reading ?? null, class: word?.class ?? null,
      spellings: [...(word?.spellings ?? new Map())].map(([spelling, sources]) => ({ spelling, code_point: pointOf(spelling), sources })),
      crops: witnesses.length, documents: new Set(witnesses.map(w => w.document ?? w.id)).size, witnesses };
  }).sort((a, b) => b.crops - a.crops || (a.id < b.id ? -1 : 1));
}

/** The senses attested for `char`, each witness dated by its document. Not cached: a claim moves no
 *  catalogue version. */
export async function sensesOf(env: Env, char: string) {
  const rows = (await env.DB.prepare(sensesQuery()).bind(char).all<SenseRow>()).results;
  if (!rows.length) return { items: [] };
  const ids = [...new Set(rows.map(r => r.object))];
  const words = (await env.DB.prepare(senseWordsQuery()).bind(JSON.stringify(ids)).all<WordRow>()).results;
  const items = senses(rows, words);
  const dated = await withDating(env, items.flatMap(s => s.witnesses), w => w.document);
  const dating = new Map(dated.map(w => [w.id, w.dating]));
  return { items: items.map(s => ({ ...s, witnesses: s.witnesses.map(w => ({ ...w, dating: dating.get(w.id) ?? {} })) })) };
}
