// The word a crop writes at its place in the text (decision 0004): the `writes_word` slot of the crop's
// current cut, read from the old database for listings that still read it. `wordSlot` shapes a slot for
// every reader; the picker's writes are in `routes/claims.ts`.
import { slotAuthors, slotValues } from './cropForms';
import { PREDICATES } from './ledger';

type Json = Record<string, any>;
const spec = PREDICATES.writes_word;
export const formOf = (confidence: number | null, scheme: string | null) => scheme === 'form'
  ? Object.entries(spec.confidence!.form).find(([, value]) => value === confidence)?.[0] ?? null : null;

export const cropWordsQuery = () => `SELECT c.subject,c.status,c.members,c.claims,c.supporting FROM current_claims c
  JOIN units u ON u.id=c.subject WHERE c.subject IN (SELECT value FROM json_each(?)) AND c.predicate='writes_word' AND c.scope='' AND c.slot=''
  AND c.crop_version IS u.crop_version`;
export const wordNamesQuery = () => 'SELECT id,reading,class FROM words WHERE id IN (SELECT value FROM json_each(?))';
export const wordMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';
const PAGE_MAX = 200;

// The claims a slot rests on: its supporting ones, or, disputed, each standing claim.
export const slotBacking = (row: Json): string[] => row.status === 'disputed'
  ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members.map((m: Json) => m.assertion)) : row.supporting;
/** A crop's word as a page shows it, from its parsed `writes_word` slot, the words' names and the
 *  methods of the claims it rests on. */
export function wordSlot(row: Json, names: Map<string, { reading: string; class: string }>, methods: Map<string, string>): Json {
  const values = slotValues(row).map(m => ({ ...(m.object ? { word: m.object, ...(names.get(m.object) ?? { reading: null, class: null }) } : { gloss: m.value.gloss }),
    form: formOf(m.confidence, m.scheme) }));
  const held = new Set(slotBacking(row).map(id => methods.get(id)));
  return { status: row.status, values, methods: spec.methods!.filter(method => held.has(method)), supporting: row.supporting, by: slotAuthors(row) };
}

/** The word slot on each crop's current cut, including competing readings when disputed. */
export async function wordsFor(env: Env, ids: string[]): Promise<Map<string, Json>> {
  const found = new Map<string, Json>(), wanted = [...new Set(ids)].slice(0, PAGE_MAX);
  if (!wanted.length) return found;
  const rows = (await env.DB.prepare(cropWordsQuery()).bind(JSON.stringify(wanted)).all<Json>()).results;
  if (!rows.length) return found;
  const parsed: Json[] = rows.map(row => ({ ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) }));
  const words = [...new Set(parsed.flatMap(row => slotValues(row).flatMap(m => m.object ? [m.object] : [])))];
  const supporting = [...new Set(parsed.flatMap(slotBacking))];
  const names = new Map(words.length ? (await env.DB.prepare(wordNamesQuery()).bind(JSON.stringify(words)).all<{ id: string; reading: string; class: string }>())
    .results.map(r => [r.id, { reading: r.reading, class: r.class }]) : []);
  const methods = new Map(supporting.length ? (await env.DB.prepare(wordMethodsQuery()).bind(JSON.stringify(supporting)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  for (const row of parsed) found.set(row.subject, wordSlot(row, names, methods));
  return found;
}

export async function withWords<T extends Json>(env: Env, items: T[]): Promise<(T & { word: Json | null })[]> {
  const words = await wordsFor(env, items.map(item => item.id));
  return items.map(item => ({ ...item, word: words.get(item.id) ?? null }));
}

// At most this many words are offered.
const CHOICES_MAX = 20;
// Spellings find their words by the spelling index, readings by a range of the word key; a reading
// typed beside the crop's characters narrows the words they spell to it.
const SPELLED = 'id IN (SELECT word FROM word_spellings WHERE spelling IN (SELECT value FROM json_each(?)))';
export const wordChoicesQuery = (by: 'spelling' | 'reading' | 'both') => `SELECT id,reading,class FROM words WHERE ${
  by === 'reading' ? 'id>=? AND id<?' : by === 'both' ? SPELLED + ' AND id>=? AND id<?' : SPELLED} ORDER BY id LIMIT ${CHOICES_MAX}`;
export const choiceSpellingsQuery = () => `SELECT DISTINCT word,spelling FROM word_spellings
  WHERE word IN (SELECT value FROM json_each(?)) ORDER BY word,spelling`;

// The key range of the word ids that begin with `prefix`.
function range(prefix: string): [string, string] {
  const points = [...prefix].map(c => c.codePointAt(0)!);
  while (points.at(-1) === 0x10ffff) points.pop();
  const next = points.pop()! + 1;
  return [prefix, String.fromCodePoint(...points, next >= 0xd800 && next <= 0xdfff ? 0xe000 : next)];
}

/** Words spelled by the crop's characters first, followed by words whose reading begins with `q`. */
export async function wordChoices(env: Env, q: string, chars: string[]) {
  const wanted = JSON.stringify([...new Set(chars)].slice(0, 4));
  const reading = q.trim().replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));
  const ids = reading ? range('ja/' + reading) : null;
  const all = (sql: string, ...values: unknown[]) => env.DB.prepare(sql).bind(...values).all<Json>().then(r => r.results);
  const spelled = chars.length ? await (ids ? all(wordChoicesQuery('both'), wanted, ...ids) : all(wordChoicesQuery('spelling'), wanted)) : [];
  const read = ids && spelled.length < CHOICES_MAX ? await all(wordChoicesQuery('reading'), ...ids) : [];
  const choices = [...new Map([...spelled, ...read].map(row => [row.id, row])).values()].slice(0, CHOICES_MAX);
  const spellings = choices.length ? await all(choiceSpellingsQuery(), JSON.stringify(choices.map(row => row.id))) : [];
  return choices.map(row => ({ ...row, spellings: spellings.filter(s => s.word === row.id).map(s => s.spelling) }));
}
