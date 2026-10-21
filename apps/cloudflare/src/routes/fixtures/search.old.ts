// The search routes as origin/main answered them from the old database, kept only so the tests can
// compare the new routes with them (routes/search.test.ts). Copied from index.ts and components.ts.
import { Problem } from '../../busy';
import { idsCharacter } from '../../category';
import { COMPONENT_SCAN, componentTerm, rankMatches, type Match } from '../../components';
import { hira } from '../../index';
import { literal } from '../../input';
import { describedCard } from '../character';

type Json = any;
type OldEnv = { DB: D1Database; ASSETS: Fetcher };
const parse = (value: string): Json => JSON.parse(value);
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
const byCodePoint = (a: string, b: string) => {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i].codePointAt(0)! - y[i].codePointAt(0)!;
  return x.length - y.length;
};
// The rarest component's characters in key order and at most COMPONENT_SCAN of them, each looked up
// by key in the list of every other component. Returns code_point, tier, size and how many of the asked
// components the character names at its top level.
export function componentMatchQuery(others: number): string {
  const joins = Array.from({ length: others }, (_, i) =>
    ` JOIN han_components o${i} ON o${i}.component=? AND o${i}.tier=x.tier AND o${i}.size=x.size AND o${i}.code_point=x.code_point AND o${i}.n>=?`).join('');
  const direct = ['x.direct', ...Array.from({ length: others }, (_, i) => `o${i}.direct`)].join('+');
  return `SELECT x.code_point,x.tier,x.size,${direct} AS direct FROM (SELECT code_point,tier,size,n,direct FROM han_components
    WHERE component=? ORDER BY tier,size,code_point LIMIT ${COMPONENT_SCAN}) x${joins} WHERE x.n>=?`;
}

async function componentSearch(env: OldEnv, wanted: Map<string, number>, limit: number) {
  const typed = [...wanted.keys()];
  const names = await env.DB.prepare(`SELECT query,component,total FROM han_component_names WHERE query IN (${typed.map(() => '?').join(',')})`)
    .bind(...typed).all<{ query: string; component: string; total: number }>();
  if (names.results.length < typed.length) return { codes: [], more: false };
  // Two typed forms of one component (⺡ and 氵) ask for it twice.
  const parts = new Map<string, { n: number; total: number }>();
  for (const row of names.results) {
    const part = parts.get(row.component);
    parts.set(row.component, { n: (part?.n ?? 0) + wanted.get(row.query)!, total: row.total });
  }
  const [driver, ...others] = [...parts].sort((a, b) => a[1].total - b[1].total);
  const found = await env.DB.prepare(componentMatchQuery(others.length))
    .bind(driver[0], ...others.flatMap(([component, { n }]) => [component, n]), driver[1].n).all<Match>();
  const ranked = rankMatches(found.results);
  return { codes: ranked.slice(0, limit).map(row => row.code_point),
    more: ranked.length > limit || driver[1].total > COMPONENT_SCAN };
}

// What the form picker's IDS editor starts from (0068, 0080): a character's descriptions and regional shapes by key, and the
// substitutes of each component they name, the `component_variants` rows naming it as `a` (by key) or
// as `b` (by `component_variant_b`), at most STRUCTURE_PARTS components a request.
export const structureQuery = () => `SELECT sequences, shapes FROM han_ids WHERE char=?`;
export const substitutesQuery = (n: number) => `SELECT a,b,count FROM component_variants WHERE a IN (${Array(n).fill('?').join(',')})
  UNION ALL SELECT a,b,count FROM component_variants WHERE b IN (${Array(n).fill('?').join(',')})`;
// A description holds at most 64 code points; the components of every sequence of one character fit here.
const STRUCTURE_PARTS = 48;
// How many substitutes a component lists, most contexts first (refs.SUBSTITUTES_SHOWN).
const SUBSTITUTES_SHOWN = 24;
const IDS_OPERATOR = /[⿰-⿿㇯〾]/u;
type Substitute = { char: string; count: number };
export async function structureOf(env: OldEnv, ctx: ExecutionContext, origin: string, char: string, version: string) {
  if (!char || [...char].length !== 1) throw new Problem(422, 'Name one character.');
  const key = new Request(`${origin}/layers/structure?c=${encodeURIComponent(char)}&v=${encodeURIComponent(version)}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json();
  const row = await env.DB.prepare(structureQuery()).bind(char).first<{ sequences: string; shapes: string | null }>();
  const sequences: string[] = row ? JSON.parse(row.sequences) : [];
  const shapes: { text: string; regions: string }[] = row?.shapes ? JSON.parse(row.shapes) : [];
  const parts = [...new Set(sequences.flatMap(text => [...text].filter(c => !IDS_OPERATOR.test(c))))].slice(0, STRUCTURE_PARTS);
  const rows = parts.length ? (await env.DB.prepare(substitutesQuery(parts.length)).bind(...parts, ...parts)
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
  ctx.waitUntil(caches.default.put(key, Response.json(found, { headers: { 'cache-control': `public, max-age=${3600}` } })));
  return found;
}
export async function suggest(env: OldEnv, q: URLSearchParams) {
  const term = (q.get('q') || '').slice(0, 128).trim();
  if (!term) return { items: [], total: 0, status: 'idle' };
  const limit = integer(q, 'limit', 8, 48);
  const rows = await env.DB.prepare(`SELECT c.data,min(a.rank) AS rank FROM aliases a JOIN characters c ON c.code_point=a.code_point
    WHERE a.query IN (?,?,?) GROUP BY c.code_point ORDER BY rank,CAST(json_extract(c.data,'$.occurrence_count') AS INTEGER) DESC,c.code_point LIMIT ?`)
    .bind(literal(term), hira(term), term.toLowerCase(), limit + 1).all<{ data: string; rank: number }>();
  if (!rows.results.length && /^[a-z0-9 -]+$/i.test(term)) {
    const found = await env.DB.prepare('SELECT data,5 AS rank FROM characters WHERE name LIKE ? LIMIT ?').bind('%'+term+'%',limit+1).all<{data:string;rank:number}>();
    rows.results.push(...found.results);
  }
  // A description sequence no alias names is the character it describes, which has no table entry.
  if (!rows.results.length && idsCharacter(literal(term))) {
    const item = { ...describedCard(literal(term)).data, rank: 0 };
    return { query: term, items: [item], total: 1, more: 0, status: 'ok', corpus: { ready: true } };
  }
  // A term no alias names that is two or more ideographs asks for the characters built from them.
  const wanted = rows.results.length ? null : componentTerm(literal(term));
  if (wanted) {
    const { codes, more } = await componentSearch(env, wanted, limit);
    const found = codes.length ? await env.DB.prepare(`SELECT code_point,data FROM characters WHERE code_point IN (${codes.map(() => '?').join(',')})`)
      .bind(...codes).all<{ code_point: string; data: string }>() : { results: [] };
    const byCode = new Map(found.results.map(r => [r.code_point, r.data]));
    const items = codes.filter(code => byCode.has(code)).map(code => ({ ...parse(byCode.get(code)!), rank: 7 }));
    return { query: term, match_kind: 'components', items, total: items.length, more: more ? 1 : 0, status: 'ok', corpus: { ready: true } };
  }
  const found = rows.results.map(r => ({ ...parse(r.data), rank: r.rank }));
  const { cards, unread } = await readingKanji(env, term, found, limit);
  found.push(...cards);
  found.push(...await searchVariants(env, literal(term), found, limit));
  const shown = found.slice(0, limit);
  const members = await unlistedForms(env, shown);
  const total = found.length + unread;
  return { query: term, items: byGrapheme(shown, members), total, more: Math.max(0,total-limit), status:'ok',corpus:{ready:true} };
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
// A list reads at most GRAPHEME_MEMBERS forms it did not find, by key.
const GRAPHEME_MEMBERS = 48;
async function unlistedForms(env: OldEnv, found: Json[]) {
  const listed = new Set(found.map(item => item.code_point));
  const wanted = [...new Set(found.flatMap(item => (item.grapheme?.members ?? []).map((m: Json) => m.code_point as string)))]
    .filter(code => !listed.has(code)).slice(0, GRAPHEME_MEMBERS);
  if (!wanted.length) return new Map<string, Json>();
  const rows = (await env.DB.prepare(`SELECT data FROM characters WHERE code_point IN (${wanted.map(() => '?').join(',')})`)
    .bind(...wanted).all<{ data: string }>()).results;
  return new Map(rows.map(r => { const card = parse(r.data); return [card.code_point as string, card] }));
}
// The kanji a kana query is a Japanese reading of (scripts/build_kanji_readings.py), after the kana it
// names: とき finds 𛄤, then 時 and 刻. The table lists a reading's kanji most attested first, so a
// list reads only the rows it shows. The readings are a static asset, kept once an isolate has read them.
let kanjiReadings: Record<string, string[]> | null = null;
async function readingTable(env: OldEnv) {
  if (kanjiReadings) return kanjiReadings;
  const response = await env.ASSETS.fetch('https://assets.invalid/refs/kanji-readings.json');
  if (!response.ok) throw new Error(`kanji readings: ${response.status}`);
  kanjiReadings = (await response.json<{ readings: Record<string, string[]> }>()).readings;
  return kanjiReadings;
}
const KANA_TERM = /^[\p{Script=Hiragana}\p{Script=Katakana}ー]+$/u;
async function readingKanji(env: OldEnv, term: string, found: Json[], limit: number): Promise<{ cards: (Json & { rank: number })[]; unread: number }> {
  if (!KANA_TERM.test(term)) return { cards: [], unread: 0 };
  let table: Record<string, string[]>;
  // A search without the readings still answers with the kana it names.
  try { table = await readingTable(env) } catch (error) { console.error(error); return { cards: [], unread: 0 } }
  const listed = new Set(found.map(item => item.char));
  const all = (table[hira(term)] ?? []).filter(char => !listed.has(char));
  const wanted = all.slice(0, Math.max(0, limit + 1 - found.length));
  if (!wanted.length) return { cards: [], unread: all.length };
  const rows = (await env.DB.prepare(`SELECT code_point,data FROM characters WHERE code_point IN (${wanted.map(() => '?').join(',')})`)
    .bind(...wanted.map(cp)).all<{ code_point: string; data: string }>()).results;
  const byCode = new Map(rows.map(r => [r.code_point, r.data]));
  const cards = wanted.filter(char => byCode.has(cp(char))).map(char => ({ ...parse(byCode.get(cp(char))!), rank: 5 }));
  return { cards, unread: all.length - wanted.length };
}
// The 異体字 of a one-character query the table holds, after what the query names: a reader who types
// 鯡 is shown 𩹉. Only the pairs the card lists as variants (a widening edge, none that keeps the pair
// apart), most attested first; a character the table lacks is never answered with one of its variants.
export const searchVariantsQuery = () => `SELECT c.data FROM (
    SELECT other,count(DISTINCT source) AS n FROM (
      SELECT b AS other,relation,widens,source FROM character_variants WHERE a=?
      UNION ALL SELECT a AS other,relation,widens,source FROM character_variants WHERE b=?)
    WHERE other<>? GROUP BY other HAVING max(widens)=1 AND max(relation IN ('simplified'))=0) v
  JOIN characters c ON c.character=v.other ORDER BY v.n DESC,v.other LIMIT ?`;
async function searchVariants(env: OldEnv, char: string, found: Json[], limit: number) {
  // A list already past the limit shows no variant; the query would only move the count.
  if ([...char].length !== 1 || found.length > limit || !found.some(item => item.char === char)) return [];
  const listed = new Set(found.map(item => item.char));
  const rows = (await env.DB.prepare(searchVariantsQuery()).bind(char, char, char, limit + listed.size + 1).all<{ data: string }>()).results;
  return rows.map(r => parse(r.data)).filter(item => !listed.has(item.char)).map(item => ({ ...item, rank: 6, variant_of: char }));
}
