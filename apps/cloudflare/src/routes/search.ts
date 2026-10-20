// Character search on the new storage layout: the names a query gives (`names` in ATLAS), the cards
// from the reference objects (`atlas3Reference.ts`, decision record 0007), the family each card files
// under from `graphemes.head`, and the IDS editor's substitutes from `component_variants`.
//
// A card's `data` is in the summary shard of its family head. A list reads the name list and one
// summary shard per family it shows; a one-character query that names a card also reads that card's
// object for its 異体字.
import { characterObject, characterStructure, componentMatches, familySummaries, nameIndex, type NameIndex } from '../atlas3Reference';
import { catalogueVersion } from '../atlas3Stamps';
import { Problem } from '../busy';
import { idsCharacter } from '../category';
import { componentTerm } from '../components';
import { describedCard, hira, literal } from '../index';

type Json = any;
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const textOf = (codePoint: string) => String.fromCodePoint(...codePoint.split(' ').map(p => parseInt(p.slice(2), 16)));
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
// Text in code point order, as SQLite compares it.
export const byCodePoint = (a: string, b: string) => {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i].codePointAt(0)! - y[i].codePointAt(0)!;
  return x.length - y.length;
};

// A hit is a card by its code point(s) and the rank of what found it.
type Hit = { code: string; rank: number; variant_of?: string };

// The names a typed term gives, as typed, in hiragana and in lower case; the best rank of each card.
export const namesQuery = () => `SELECT code_point,min(rank) AS rank FROM names WHERE text IN (?,?,?) GROUP BY code_point`;
// Each character's family head, by the `graphemes` text key; a character with no head is its own family.
export const headsQuery = () => `SELECT g.text,h.text AS head FROM graphemes g LEFT JOIN graphemes h ON h.id=g.head
  WHERE g.text IN (SELECT value FROM json_each(?))`;

// The cards the names give, best rank first, then the most frequent, then by code point text: at most
// `limit`. `names` files a name under one code point; a card of several code points is named by its
// own text and by its code points in lower case, at rank 0, which the name list answers.
async function aliasHits(env: Env, index: NameIndex, term: string, limit: number): Promise<Hit[]> {
  const typed = literal(term), lower = term.toLowerCase();
  const rows = (await env.ATLAS.prepare(namesQuery()).bind(typed, hira(term), lower).all<{ code_point: number; rank: number }>()).results;
  const ranks = new Map<string, number>();
  for (const row of rows) {
    const code = 'U+' + row.code_point.toString(16).toUpperCase().padStart(4, '0');
    if (index.occurrences.has(code)) ranks.set(code, row.rank);
  }
  const spelled = /^u\+[0-9a-f]{4,6}( u\+[0-9a-f]{4,6})+$/.test(lower) ? [lower.toUpperCase()] : [];
  for (const code of [cp(typed), cp(hira(term)), cp(lower), ...spelled])
    if (code.includes(' ') && index.occurrences.has(code)) ranks.set(code, 0);
  return [...ranks].map(([code, rank]) => ({ code, rank }))
    .sort((a, b) => a.rank - b.rank || index.occurrences.get(b.code)! - index.occurrences.get(a.code)! || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0))
    .slice(0, limit);
}

// The `data` of the cards of each family the codes file under, by code point: one summary shard per family.
async function cardsOf(env: Env, codes: string[], ctx?: ExecutionContext): Promise<Map<string, Json>> {
  if (!codes.length) return new Map();
  const rows = (await env.ATLAS.prepare(headsQuery()).bind(JSON.stringify([...new Set(codes)].map(textOf)))
    .all<{ text: string; head: string | null }>()).results;
  const heads = new Map(rows.map(row => [cp(row.text), row.head === null ? cp(row.text) : cp(row.head)]));
  return await familySummaries(env, codes.map(code => heads.get(code) ?? code), ctx);
}

// The kanji a kana query is a Japanese reading of (scripts/build_kanji_readings.py), after the kana it
// names: とき finds 𛄤, then 時 and 刻. The table lists a reading's kanji most attested first, so a
// list takes only the ones it can show. The readings are a static asset, kept once an isolate has read them.
let kanjiReadings: Record<string, string[]> | null = null;
async function readingTable(env: Env) {
  if (kanjiReadings) return kanjiReadings;
  const response = await env.ASSETS.fetch('https://assets.invalid/refs/kanji-readings.json');
  if (!response.ok) throw new Error(`kanji readings: ${response.status}`);
  kanjiReadings = (await response.json<{ readings: Record<string, string[]> }>()).readings;
  return kanjiReadings;
}
const KANA_TERM = /^[\p{Script=Hiragana}\p{Script=Katakana}ー]+$/u;
async function readingKanji(env: Env, index: NameIndex, term: string, found: Hit[], limit: number): Promise<{ hits: Hit[]; unread: number }> {
  if (!KANA_TERM.test(term)) return { hits: [], unread: 0 };
  let table: Record<string, string[]>;
  // A search without the readings still answers with the kana it names.
  try { table = await readingTable(env) } catch (error) { console.error(error); return { hits: [], unread: 0 } }
  const listed = new Set(found.map(hit => textOf(hit.code)));
  const all = (Object.hasOwn(table, hira(term)) ? table[hira(term)] : []).filter(char => !listed.has(char));
  const wanted = all.slice(0, Math.max(0, limit + 1 - found.length));
  return { hits: wanted.filter(char => index.occurrences.has(cp(char))).map(char => ({ code: cp(char), rank: 5 })), unread: all.length - wanted.length };
}

// The 異体字 of a one-character query, after what the query names: a reader who types 鯡 is shown 𩹉.
// Only the characters the card's edges call variants (a widening edge, none that keeps the pair apart),
// most sources first, then by character; a character no card has is never answered with one of its variants.
async function searchVariants(env: Env, index: NameIndex, char: string, found: Hit[], limit: number, ctx?: ExecutionContext): Promise<Hit[]> {
  // A list already past the limit shows no variant; the edges would only move the count.
  if ([...char].length !== 1 || found.length > limit || !found.some(hit => hit.code === cp(char))) return [];
  const card = await characterObject(env, cp(char), ctx);
  const others = new Map<string, { sources: Set<string>; widens: boolean; apart: boolean }>();
  for (const [other, relation, source, , , widens] of card?.variants ?? []) {
    if (other === char) continue;
    const edge = others.get(other) ?? { sources: new Set<string>(), widens: false, apart: false };
    edge.sources.add(source);
    edge.widens ||= widens === 1;
    edge.apart ||= relation === 'simplified';
    others.set(other, edge);
  }
  const listed = new Set(found.map(hit => hit.code));
  return [...others].filter(([other, edge]) => edge.widens && !edge.apart && index.occurrences.has(cp(other)))
    .sort((a, b) => b[1].sources.size - a[1].sources.size || byCodePoint(a[0], b[0]))
    .slice(0, limit + listed.size + 1)
    .filter(([other]) => !listed.has(cp(other)))
    .map(([other]) => ({ code: cp(other), rank: 6, variant_of: char }));
}

// Each grapheme a list names, once, at the place of its first form there: the forms the query found in
// their order, then every other form of the grapheme. 时 lists 時 = 时 = 旹, ね lists ね, ネ and 𛂒 to 𛄧.
const familyOf = (item: Json): string => item.grapheme?.code_point ?? item.code_point;
export function byGrapheme(found: Json[], members: Map<string, Json>) {
  const groups = new Map<string, Json[]>();
  for (const item of found) groups.set(familyOf(item), [...(groups.get(familyOf(item)) ?? []), item]);
  return [...groups.values()].flatMap(group => {
    const listed = new Set(group.map(item => item.code_point));
    const rest = (group[0].grapheme?.members ?? []).filter((m: Json) => !listed.has(m.code_point) && members.has(m.code_point))
      .map((m: Json) => ({ ...members.get(m.code_point)!, rank: 8 }));
    return [...group, ...rest];
  });
}
// A list shows at most GRAPHEME_MEMBERS forms it did not find. A grapheme's forms file under its
// family, so their cards are in the shards the list has read.
const GRAPHEME_MEMBERS = 48;
function unlistedForms(found: Json[], cards: Map<string, Json>) {
  const listed = new Set(found.map(item => item.code_point));
  const wanted = [...new Set(found.flatMap(item => (item.grapheme?.members ?? []).map((m: Json) => m.code_point as string)))]
    .filter(code => !listed.has(code)).slice(0, GRAPHEME_MEMBERS);
  return new Map(wanted.filter(code => cards.has(code)).map(code => [code, cards.get(code)]));
}

export async function suggest(env: Env, q: URLSearchParams, ctx?: ExecutionContext) {
  const term = (q.get('q') || '').slice(0, 128).trim();
  if (!term) return { items: [], total: 0, status: 'idle' };
  const limit = integer(q, 'limit', 8, 48);
  const index = await nameIndex(env, ctx) ?? { occurrences: new Map<string, number>(), named: () => [] };
  const found = await aliasHits(env, index, term, limit + 1);
  if (!found.length && /^[a-z0-9 -]+$/i.test(term)) found.push(...index.named(term, limit + 1).map(code => ({ code, rank: 5 })));
  // A description sequence no name gives is the character it describes, which has no card.
  if (!found.length && idsCharacter(literal(term))) {
    const item = { ...describedCard(literal(term)).data, rank: 0 };
    return { query: term, items: [item], total: 1, more: 0, status: 'ok', corpus: { ready: true } };
  }
  // A term no name gives that is two or more ideographs asks for the characters built from them.
  const wanted = found.length ? null : componentTerm(literal(term));
  if (wanted) {
    const { codes, more } = await componentMatches(env, wanted, limit, ctx);
    const held = codes.filter(code => index.occurrences.has(code)), cards = await cardsOf(env, held, ctx);
    const items = held.filter(code => cards.has(code)).map(code => ({ ...cards.get(code), rank: 7 }));
    return { query: term, match_kind: 'components', items, total: items.length, more: more ? 1 : 0, status: 'ok', corpus: { ready: true } };
  }
  const { hits, unread } = await readingKanji(env, index, term, found, limit);
  found.push(...hits);
  found.push(...await searchVariants(env, index, literal(term), found, limit, ctx));
  const hitsShown = found.slice(0, limit), cards = await cardsOf(env, hitsShown.map(hit => hit.code), ctx);
  const shown = hitsShown.filter(hit => cards.has(hit.code)).map(hit => ({ ...cards.get(hit.code), rank: hit.rank,
    ...(hit.variant_of === undefined ? {} : { variant_of: hit.variant_of }) }));
  const total = found.length + unread;
  return { query: term, items: byGrapheme(shown, unlistedForms(shown, cards)), total, more: Math.max(0, total - limit), status: 'ok', corpus: { ready: true } };
}

// What the form picker's IDS editor starts from (0068, 0080): a character's descriptions and regional
// shapes from its IDS shard, and the substitutes of each component they name, the `component_variants`
// rows naming it as `a` (by key) or as `b` (by `component_variant_b`), at most STRUCTURE_PARTS components a request.
export const substitutesQuery = (n: number) => `SELECT a,b,count FROM component_variants WHERE a IN (${Array(n).fill('?').join(',')})
  UNION ALL SELECT a,b,count FROM component_variants WHERE b IN (${Array(n).fill('?').join(',')})`;
// A description holds at most 64 code points; the components of every sequence of one character fit here.
const STRUCTURE_PARTS = 48;
// How many substitutes a component lists, most contexts first (refs.SUBSTITUTES_SHOWN).
const SUBSTITUTES_SHOWN = 24;
const IDS_OPERATOR = /[⿰-⿿㇯〾]/u;
// Answers that change only with the catalogue are kept at the edge an hour.
const KEPT = 3600;
type Substitute = { char: string; count: number };
export async function structureOf(env: Env, ctx: ExecutionContext, origin: string, char: string) {
  if (!char || [...char].length !== 1) throw new Problem(422, 'Name one character.');
  const key = new Request(`${origin}/layers/structure?c=${encodeURIComponent(char)}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json();
  const structure = await characterStructure(env, char, ctx);
  const sequences: string[] = structure?.sequences ?? [];
  const shapes: { text: string; regions: string }[] = structure?.shapes ?? [];
  const parts = [...new Set(sequences.flatMap(text => [...text].filter(c => !IDS_OPERATOR.test(c))))].slice(0, STRUCTURE_PARTS);
  const rows = parts.length ? (await env.ATLAS.prepare(substitutesQuery(parts.length)).bind(...parts, ...parts)
    .all<{ a: string; b: string; count: number }>()).results : [];
  const substitutes: Record<string, Substitute[]> = {};
  for (const part of parts) {
    const found = rows.filter(r => r.a === part || r.b === part)
      .map(r => ({ char: r.a === part ? r.b : r.a, count: r.count }))
      .sort((x, y) => y.count - x.count || byCodePoint(x.char, y.char))
      .slice(0, SUBSTITUTES_SHOWN);
    if (found.length) substitutes[part] = found;
  }
  const found = { char, sequences, shapes, substitutes };
  ctx.waitUntil(caches.default.put(key, Response.json(found, { headers: { 'cache-control': `public, max-age=${KEPT}` } })));
  return found;
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
  const all = (sql: string, ...values: unknown[]) => env.ATLAS.prepare(sql).bind(...values).all<Json>().then(r => r.results);
  const spelled = chars.length ? await (ids ? all(wordChoicesQuery('both'), wanted, ...ids) : all(wordChoicesQuery('spelling'), wanted)) : [];
  const read = ids && spelled.length < CHOICES_MAX ? await all(wordChoicesQuery('reading'), ...ids) : [];
  const choices = [...new Map([...spelled, ...read].map(row => [row.id, row])).values()].slice(0, CHOICES_MAX);
  const spellings = choices.length ? await all(choiceSpellingsQuery(), JSON.stringify(choices.map(row => row.id))) : [];
  return choices.map(row => ({ ...row, spellings: spellings.filter(s => s.word === row.id).map(s => s.spelling) }));
}
