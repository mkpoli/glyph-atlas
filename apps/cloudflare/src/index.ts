// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
import { ROUND_MAX } from './rounds';
import { held } from './held';
import { body, canonical, compose, graphemeKey, graphemeText, integer, literal, single, text } from './input';
export { canonical, compose, literal, single } from './input';
import { RECORD_MAX, readRecords, recordKey, type RecordPointer } from './records';
export { recordRanges } from './records';
import { formsRoute, withForm, formed, FORM_COLUMNS, type FormTools, type UnitForm } from './forms';
import { similarCrops } from './similar';
import { cropParam, movedPathResponse } from './atlas3Crops';
import { drawingFile } from './drawings';
import { MAX_BODY, QueryError, addressKey, boundedText, modelFile, modelInfo, query as imageQuery } from './reverse';
import { componentSearch, componentTerm } from './components';
import { formsFor, withForms } from './cropForms';
import { wordChoices } from './cropWords';
import { categoryOf, idsCharacter } from './category';
import { auth, claim, owned, providers, viewer } from './auth';
import { AVATAR_PATH, avatar, setAvatar } from './avatar';
import { ranking } from './routes/ranking';
import { connections } from './connections';
import { flagCorpus, flaggedCorpus, reject, reviewerActors, reviewers, submissions } from './routes/admin';
import { history } from './routes/history';
import { listingItems } from './atlas3Listing';
import { StaleCrop, staleResponse } from './atlas3Crops';
import { Problem, READ_BUDGET, RETRY_AFTER, StorageUnavailable, described, retried, transient } from './busy';
import { dealable, reviewRoute } from './routes/review';
import { pausedCallback, pausedResponse, pausedSignOut, writes, writesPaused } from './freeze';
import { claimRead, claimWrite, type ClaimTools } from './routes/claims';
import { honkokuPage } from './honkoku';
import { FavouriteError, favouriteCrops, favouriteIds, setFavourite } from './routes/favourites';
import { MediaIncomplete, mediaResponse } from './atlas3Media';
import { cropRoute } from './routes/crop';
import { runsRoute } from './routes/runs';
import { datingOf, documentOf } from './dating';
import { cards, characterCard, characterPage, chronology, byCodePoint, citeCharacter, corpusOccurrences, decades, describedCard, jamoCard, occurrences, type CharacterTools } from './routes/character';
export { leastTypicalQuery } from './forms';
export { componentMatchQuery } from './components';
export { datingQuery, tallyDates } from './dating';
import * as browse from './routes/browse';
export { claimHistoryQuery, currentClaimsQuery, ledgerActionsQuery, ledgerClaimsQuery, resolveClearQuery, resolveWriteQuery } from './ledger';
export { cropFormsQuery, formNamesQuery } from './cropForms';
type Json = Record<string, any>;
type UnitRow = { id: string; origin: string; character: string | null; state: string; revision: number;
  quiz: number; category?: string; data: string; snapshot: string; context: string; visual: string; style?: string;
  document?: string | null;
  // The crop's evidence version (0047): its id, image checksum and box, as SQLite joins them.
  crop_version?: string | null;
  // A corpus glyph nothing has named yet: it has no `units` row, and this is where it is published.
  fresh?: CorpusRow };
type CorpusRow = {id:string;character:string|null;family:string|null;visual_group:string|null;production:string;style:string;shuffle:number;object:string;offset:number;size:number};
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const parse = (value: string): Json => JSON.parse(value);
const unavailable = { status: 'unavailable', candidates: [] };
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const toneMark = (char: string) => [0x302E, 0x302F].includes(char.codePointAt(0)!);
const choseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x1100 && n <= 0x115F) || (n >= 0xA960 && n <= 0xA97F);
};
const jungseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x1160 && n <= 0x11A7) || (n >= 0xD7B0 && n <= 0xD7C6);
};
const jongseong = (char: string) => {
  const n = char.codePointAt(0)!; return (n >= 0x11A8 && n <= 0x11FF) || (n >= 0xD7CB && n <= 0xD7FF);
};
const FILLERS = [0x115F, 0x1160];
/** An old-Hangul syllable Unicode has no precomposed code point for, checked in NFD so that the NFC
 * spelling 셰 + ᇰ of U+1109 U+1168 U+11F0 is one; text NFC would change has another key. */
export const jamoSyllable = (value: string) => {
  if (!single(value) || [...value].length < 2 || value.normalize('NFC') !== value) return false;
  const chars = [...value.normalize('NFD')]; let i = 0;
  if (chars.every(c => FILLERS.includes(c.codePointAt(0)!))) return false;
  while (i < chars.length && choseong(chars[i])) i++;
  if (!i) return false;
  const vowels = i;
  while (i < chars.length && jungseong(chars[i])) i++;
  if (i === vowels) return false;
  while (i < chars.length && jongseong(chars[i])) i++;
  if (i < chars.length && toneMark(chars[i])) i++;
  return i === chars.length;
};
export const hira = (value: string) => [...literal(value)].map(c => {
  const n = c.codePointAt(0)!; return n >= 0x30a1 && n <= 0x30f6 ? String.fromCodePoint(n - 0x60) : c;
}).join('');
async function meta(env: Env, key: string): Promise<any> {
  const row = await env.DB.prepare('SELECT value FROM metadata WHERE key=?').bind(key).first<{ value: string }>();
  return row ? JSON.parse(row.value) : null;
}
// How many retirements a redirect follows; a publication points a retired crop at a current one, so
// a longer chain is a loop.
const REDIRECT_HOPS = 8;
export async function replacement(env: Env, id: string): Promise<string | null> {
  let target: string | null = null;
  for (let hop = 0, current = id; hop < REDIRECT_HOPS; hop++) {
    const next = await env.DB.prepare('SELECT target FROM unit_redirects WHERE id=?').bind(current).first<{ target: string }>();
    if (!next) return target;
    target = current = next.target;
  }
  return null;
}
async function unit(env: Env, id: string): Promise<UnitRow> {
  const row = await env.DB.prepare('SELECT * FROM units WHERE id=?').bind(id).first<UnitRow>();
  if(row&&row.origin!=='retired')return row;
  const pointer=row?null:await env.DB.prepare('SELECT * FROM corpus_units WHERE id=?').bind(id).first<CorpusRow>();
  if(!pointer){
    // A retired crop, kept for its history or deleted, is answered with the crop that replaced it.
    const target=await replacement(env,id);
    if(target)throw new Problem(404,'This crop was replaced.',{replaced_by:target});
    throw new Problem(404, 'This character is not in the published collection.');
  }
  const data=await corpusData(env,pointer);
  return {id,origin:'corpus',character:data.written_character??null,state:data.state,revision:data.revision,quiz:dealable('corpus',data)?1:0,
    category:categoryOf(data.label),data:JSON.stringify(data),snapshot:JSON.stringify(data),
    context:JSON.stringify(unavailable),visual:JSON.stringify(unavailable),fresh:pointer};
}
// The first round or review that names a corpus glyph writes its `units` row from the published record,
// in the same batch and before the rows that reference it.
function materialise(env:Env,row:UnitRow&{fresh:CorpusRow}){
  const d=parse(row.data);
  return env.DB.prepare(`INSERT OR IGNORE INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,
    data,snapshot,context,visual,document,style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
    .bind(row.id,'corpus',d.written_character||null,d.grapheme||(d.written_character?cp(d.written_character):null),d.visual_group?.id||null,row.fresh.production,
      row.category||categoryOf(d.label),d.state,d.revision,row.quiz,1,row.fresh.shuffle,row.data,row.snapshot,row.context,row.visual,null,row.fresh.style);
}
async function corpusData(env:Env,row:CorpusRow):Promise<Json>{
  if(row.size>RECORD_MAX)throw new Problem(503,'Invalid published record.');
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object)throw new Problem(503,'The corpus publication is incomplete.');
  return withForm(env,await object.json<Json>(),formTools);
}
// Corpus records with their form decisions, read in one query; a failed query fails the call (`readRecords`).
async function corpusRecords(env: Env, pointers: RecordPointer[]): Promise<Map<string, Json>> {
  const raw = await readRecords(env, pointers);
  const ids = [...new Set([...raw.values()].map(r => r.id).filter(Boolean))];
  const forms = new Map(ids.length ? (await env.DB.prepare(`SELECT ${FORM_COLUMNS} FROM form_units WHERE id IN (SELECT value FROM json_each(?))`)
    .bind(JSON.stringify(ids)).all<UnitForm>()).results.map(row => [row.id, row] as const) : []);
  return new Map([...raw].map(([key, record]) => [key, formed(record, forms.get(record.id) ?? null, formTools)]));
}
// A record `corpusRecords` read, or else the record read alone (`corpusData`), so one it could not read
// fails as it always did: a lost connection reaches the retries, a missing record is a 503.
async function recordOf(env: Env, read: Map<string, Json>, row: CorpusRow): Promise<Json> {
  return read.get(recordKey(row)) ?? await corpusData(env, row);
}
// Listing items for crops by id: rows the site holds, then corpus glyphs from their published records.
// An id it does not hold, or a retired crop, is left out.
async function itemsFor(env: Env, ids: string[]): Promise<Map<string, Json>> {
  const found = new Map<string, Json>();
  if (!ids.length) return found;
  const marks = (n: number) => Array(n).fill('?').join(',');
  const rows = await env.DB.prepare(`SELECT * FROM units WHERE id IN (${marks(ids.length)}) AND origin!='retired'`)
    .bind(...ids).all<UnitRow>();
  for (const row of rows.results) found.set(row.id, { ...compact(row), origin: row.origin });
  const forms = await formsFor(env, [...found.keys()]);
  for (const [id, item] of found) item.form = forms.get(id) ?? null;
  const rest = ids.filter(id => !found.has(id));
  if (rest.length) {
    const pointers = await env.DB.prepare(`SELECT * FROM corpus_units WHERE id IN (${marks(rest.length)})`)
      .bind(...rest).all<CorpusRow>();
    // A record that cannot be read, or a batch that fails whole, leaves out only the glyphs it holds.
    const read = await corpusRecords(env, pointers.results).catch(() => new Map<string, Json>());
    for (const p of pointers.results) {
      const record = read.get(recordKey(p)) ?? await corpusData(env, p).catch(() => null);
      if (record) found.set(p.id, { ...listing(record), origin: 'corpus' });
    }
  }
  const dating = await datingOf(env, [...found.values()].map(documentOf));
  for (const [id, item] of found) found.set(id, { ...item, dating: dating.get(documentOf(item) ?? '') ?? {} });
  return found;
}
function compact(row: UnitRow): Json {
  return { ...listing(parse(row.data)), ...(row.document ? { document: row.document } : {}), ...(row.style ? { style: row.style } : {}) };
}
// A record as a listing shows it, without the fields only its inspector needs.
function listing(d: Json): Json {
  const { text, line, context_image, context_box, crop_box, ...rest } = d;
  return rest;
}
/** Units by id, read eight at a time; an id the collection does not hold maps to its 404. */
async function unitsById(env: Env, ids: string[]): Promise<Map<string, UnitRow | Problem>> {
  const found = new Map<string, UnitRow | Problem>();
  for (const part of chunks(ids, 8)) {
    const rows = await Promise.all(part.map(id => unit(env, id).catch(error => { if (error instanceof Problem && error.status === 404) return error; throw error })));
    part.forEach((id, i) => found.set(id, rows[i]));
  }
  return found;
}
// What the cached listings are keyed by: every way `units` and the review tables change moves it. A
// publication stamps `published_at`, `refresh_published_units.py` stamps `units_refreshed_at`, a
// review or undo adds an event, a round adds a submission, and an undo of a round with no review
// only marks its submission undone. An isolate keeps the stamps it read for `CATALOGUE_LAG` seconds,
// so while reviews come in every copy keyed by them moves once a minute rather than on each review,
// and most requests ask the database nothing for them.
type CatalogueStamps = { published: string | null; refreshed: string | null; event: number | null; submission: number | null; undone: number };
const heldStamps = held<CatalogueStamps | null>();
function catalogueStamps(env: Env): Promise<CatalogueStamps | null> {
  return heldStamps(() => env.DB.prepare(`SELECT (SELECT value FROM metadata WHERE key='published_at') AS published,
    (SELECT value FROM metadata WHERE key='units_refreshed_at') AS refreshed,
    (SELECT max(rowid) FROM events) AS event,(SELECT max(rowid) FROM submissions) AS submission,
    (SELECT count(*) FROM submissions WHERE undone=1) AS undone`).first<CatalogueStamps>(), Date.now(), Number(env.CATALOGUE_LAG));
}
const catalogueKey = (version: CatalogueStamps | null) =>
  [version?.published ?? '', version?.refreshed ?? '', version?.event ?? 0, version?.submission ?? 0, version?.undone ?? 0].join(':');
async function catalogueVersion(env: Env): Promise<string> {
  return catalogueKey(await catalogueStamps(env));
}
// How long the edge keeps a copy of an answer every visitor shares, under its catalogue version.
const FACETS_TTL = 3600;
// The corpus recount a cached corpus answer is keyed by: every recount a publication runs stamps
// `corpus_counts_at`, and so does the forms drain once a decision has moved glyphs between characters.
async function corpusVersion(env: Env) {
  return (await env.DB.prepare("SELECT value FROM metadata WHERE key='corpus_counts_at'").first<{ value: string }>())?.value ?? '';
}
// The characters a grapheme is written as, by the character table: は's family is は, ハ and its
// hentaigana, and ば's is ば, バ and each hentaigana of は with U+3099. A key that names no family
// (ツ + U+309A, or a character the table lacks) is its own text alone; a key that names a family's
// member rather than the family is refused, a sequence such as 𛂞 + U+3099 as much as a character.
// A key's own text is composed, as every written character is stored.
export async function graphemeMembers(env: Env, key: string): Promise<string[]> {
  const row = await env.DB.prepare("SELECT json_extract(data,'$.grapheme') AS grapheme FROM characters WHERE code_point=?").bind(key).first<{ grapheme: string | null }>();
  const family = row?.grapheme ? parse(row.grapheme) as { code_point: string; char: string; members?: { char: string }[] } : null;
  if (family && family.code_point !== key) throw new Problem(422, `${graphemeText(key)} is filed under ${family.char}.`);
  return family?.members?.length ? family.members.map(member => member.char) : [compose(graphemeText(key))];
}
function chunks<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
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
async function structureOf(env: Env, ctx: ExecutionContext, origin: string, char: string) {
  if (!char || [...char].length !== 1) throw new Problem(422, 'Name one character.');
  const key = new Request(`${origin}/layers/structure?c=${encodeURIComponent(char)}&v=${encodeURIComponent(await catalogueVersion(env))}`);
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
  ctx.waitUntil(caches.default.put(key, Response.json(found, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return found;
}
async function known(env: Env, value: string) {
  const text = literal(value);
  const key = cp(text);
  const row = await env.DB.prepare('SELECT data,detail FROM characters WHERE code_point=?').bind(key).first<{data:string;detail:string}>();
  if (row) return { data: parse(row.data), detail: parse(row.detail) };
  if (idsCharacter(text)) return describedCard(text);
  if (jamoSyllable(text)) return jamoCard(text);
  throw new Problem(404, 'Character not found.');
}
export async function suggest(env: Env, q: URLSearchParams) {
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
async function unlistedForms(env: Env, found: Json[]) {
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
async function readingTable(env: Env) {
  if (kanjiReadings) return kanjiReadings;
  const response = await env.ASSETS.fetch('https://assets.invalid/refs/kanji-readings.json');
  if (!response.ok) throw new Error(`kanji readings: ${response.status}`);
  kanjiReadings = (await response.json<{ readings: Record<string, string[]> }>()).readings;
  return kanjiReadings;
}
const KANA_TERM = /^[\p{Script=Hiragana}\p{Script=Katakana}ー]+$/u;
async function readingKanji(env: Env, term: string, found: Json[], limit: number): Promise<{ cards: (Json & { rank: number })[]; unread: number }> {
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
async function searchVariants(env: Env, char: string, found: Json[], limit: number) {
  // A list already past the limit shows no variant; the query would only move the count.
  if ([...char].length !== 1 || found.length > limit || !found.some(item => item.char === char)) return [];
  const listed = new Set(found.map(item => item.char));
  const rows = (await env.DB.prepare(searchVariantsQuery()).bind(char, char, char, limit + listed.size + 1).all<{ data: string }>()).results;
  return rows.map(r => parse(r.data)).filter(item => !listed.has(item.char)).map(item => ({ ...item, rank: 6, variant_of: char }));
}
// Open listings and citations can be read by other sites.
const OPEN = { 'access-control-allow-origin': '*' };
// Reads any site may make: a document's characters and a citation.
const opened = (path: string) => path.startsWith('/atlas/documents/') || path.startsWith('/atlas/cite/');
// A character's grapheme family, as a reviewed correction takes it; one the catalogue lacks is its own.
const characterTools: CharacterTools = { literal, jamoSyllable };
const formTools: FormTools = {fail:(status,message,extra)=>{throw new Problem(status,message,extra)},body,text,codePoints:cp,
  family:async(env,char)=>(await known(env,char).catch(()=>null))?.data.grapheme?.code_point||cp(char)};
// What the ledger's routes need of the rest of the Worker (`routes/claims.ts`).
const claimTools: ClaimTools = {fail:(status,message,extra)=>{throw new Problem(status,message,extra)},body,text,canonical:value=>canonical(value),literal,integer};
// Undo a submission as `actor`: each crop goes back to what it was before, unless a later review has
// changed it since. A rejection is the same undo made by an admin, recorded with its reason.
async function revert(env:Env,submission:Json,actor:string,rejection?:{reason:string;reviewer?:[string,string[]]}){
  const key=submission.id as string;
  if(submission.undone)return {results:[],duplicate:true};
  const rows=await env.DB.prepare("SELECT * FROM events WHERE submission=? AND kind='review'").bind(key).all<Json>();
  const statements:D1PreparedStatement[]=[],results=[];const at=new Date().toISOString();
  for(const r of rows.results){
    const current=await unit(env,r.target);
    // Rejecting all of one reviewer's work also passes over their own later changes to the crop that
    // are already undone, and the undos themselves; anyone else's later change, or one of theirs that
    // still stands, stops it.
    const later=current.revision!==r.expected_revision+1&&(!rejection?.reviewer||await env.DB.prepare(`SELECT 1 FROM events e LEFT JOIN submissions s ON s.id=e.submission
      WHERE e.target=? AND e.expected_revision>? AND (s.actor IS NULL OR s.actor NOT IN ${rejection.reviewer[0]} OR (s.undone=0 AND e.kind='review')) LIMIT 1`)
      .bind(r.target,r.expected_revision,...rejection.reviewer[1]).first());
    if(later)throw new Problem(409,'A later review changed this crop. It cannot be undone.');
    // A record saved before 0056 carries a reading; the crop it restores does not.
    const {reading:_reading,...before}=parse(r.before_data);
    const restored={...before,revision:current.revision+1};
    const event={...parse(r.event),id:'cf:'+crypto.randomUUID(),old:parse(r.event).new,new:parse(r.event).old,evidence:'undo of '+r.id,at};
    statements.push(env.DB.prepare('INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)')
      .bind(event.id,key,r.target,actor,current.revision,current.data,JSON.stringify(restored),JSON.stringify(event),r.snapshot,'undo',at));
    // Restore queue eligibility from the record the undo restores, with the repair verdict the row holds
    // now: the event trigger keeps it, and a publication may have changed it since the review.
    const eligible={...parse(r.before_data),repair:parse(current.data).repair};
    statements.push(env.DB.prepare('UPDATE units SET quiz=? WHERE id=?').bind(dealable(current.origin,eligible)?1:0,r.target));
    results.push({id:event.id,target_id:r.target,revision:restored.revision,review:event});
  }
  statements.push(env.DB.prepare('UPDATE submissions SET undone=1 WHERE id=?').bind(key));
  if(rejection)statements.push(env.DB.prepare('INSERT INTO rejections(submission,by,reason,at) VALUES(?,?,?,?)').bind(key,actor,rejection.reason,at));
  try{await env.DB.batch(statements)}catch(error){if(String(error).includes('review_revision_conflict'))throw new Problem(409,'A later review changed this crop.');throw error}
  return {results};
}
// A review keeps the words it was saved with: one saved while crops carried a reading names its typed
// characters `suggested_reading`.
export const typedText = (evidence: Json | null | undefined): string | null =>
  evidence?.suggested_text ?? evidence?.suggested_reading ?? null;
async function reviews(env:Env,all:boolean){
  const rows=await env.DB.prepare(`SELECT e.event,e.snapshot,e.expected_revision,u.revision,u.origin,e.id,u.snapshot AS publication_snapshot,
    EXISTS(SELECT 1 FROM events newer WHERE newer.target=e.target AND newer.expected_revision>e.expected_revision) AS superseded
    FROM events e JOIN units u ON u.id=e.target WHERE e.kind='review' ${all?'':'AND e.processed=0'} ORDER BY e.at`).all<Json>();
  return {version:1,kind:'atlas-character-reviews',reviews:rows.results.map(r=>{
    const event=parse(r.event),snapshot=parse(r.snapshot);
    if(r.origin==='corpus'){const e=parse(event.evidence);return {origin:'corpus',event:{...event,new:{verdict:e.verdict,issue:e.issue,
      character:e.suggested_character?literal(e.suggested_character):null,correction:typedText(e),note:e.note}},
      reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      source_update:{identity:event.target_id,source_revision:snapshot.source_revision,source:snapshot.source,box:snapshot.box,
        original_character:snapshot.source_code_point?literal(snapshot.source_code_point):snapshot.source_label,
        proposed_character:e.suggested_character?literal(e.suggested_character):null,issue:e.issue,applied_upstream:false}}}
    return {event,reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      expected_revision:r.expected_revision,publication_snapshot:parse(r.publication_snapshot)}
  }),publication:await meta(env,'published_at')};
}
const routes = {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
      // The write freeze: one check before any route, so no write is reached while it holds.
      if(writesPaused(env)&&writes(request.method,path)){
        if(request.method==='POST'&&path==='/api/auth/sign-out')return pausedSignOut(request,auth(env,url.origin).options);
        if(path.startsWith('/api/auth/callback/'))return pausedCallback(url.origin);
        return pausedResponse(opened(path)?OPEN:{});
      }
      if(path.startsWith('/api/auth/'))return await auth(env,url.origin).handler(request);
      if(request.method==='POST'&&path==='/atlas/similar/query'){
        // An image search writes nothing and needs no session; one address is held to a rate. The
        // body is a vector, never an image, and it is neither stored nor logged.
        const {success}=await env.REVERSE_QUERIES.limit({key:addressKey(request.headers.get('cf-connecting-ip'))});
        if(!success)throw new Problem(429,'Too many image searches at once. Wait a minute and try again.');
        const text=await boundedText(request,MAX_BODY);
        if(text===null)throw new Problem(413,'The search request is too large.');
        let input:Json;try{input=JSON.parse(text)}catch{throw new Problem(400,'The search request is not JSON.')}
        if(!input||typeof input!=='object'||Array.isArray(input))throw new Problem(400,'The search request is not an object.');
        try{return json(await imageQuery(env,input,listingItems,ctx))}
        catch(error){if(error instanceof QueryError)throw new Problem(error.status,error.message,error.extra);throw error}
      }
      if(request.method==='POST'){
        // Every write is made by the user the request is signed in as; a browser starts an anonymous
        // session before its first one.
        const me=await viewer(env,request,true);
        if(!me)throw new Problem(401,'Sign in to save.');
        // An admin's page: rejecting what a reviewer saved. Banning and roles are Better Auth's own.
        if(path.startsWith('/api/admin/')&&!me.admin)throw new Problem(403,'Only an admin can do this.');
        if(path==='/api/admin/reject')return json(await reject(env,me.id,await body(request),{text}));
        if(path==='/api/admin/corpus-flags')return json(await flagCorpus(env,me.id,await body(request),{text}));
        if(path==='/api/account/avatar'){
          const origin=request.headers.get('origin');
          if(origin&&origin!==url.origin)throw new Problem(403,'Use the account page on this site.');
          const {status,body:out}=await setAvatar(env,me,q.get('source')??'',request);return json(out,status);
        }
        if(path==='/api/favourites'){
          try{return json(await setFavourite(env,me.id,await body(request),ctx))}
          catch(error){if(error instanceof FavouriteError)throw new Problem(error.status,error.message);if(error instanceof StaleCrop)return staleResponse();throw error}
        }
        if(path==='/api/account/claim'){const {status,body:out}=await claim(env,me,String((await body(request)).reviewer??''));return json(out,status)}
        const reviewed=await reviewRoute(env,request,path,me.id);
        if(reviewed)return reviewed;
        const claimed=await claimWrite(env,request,path,me,claimTools,async()=>{
          const {success}=await env.CLAIMS.limit({key:addressKey(request.headers.get('cf-connecting-ip'))});
          if(!success)throw new Problem(429,'Too many claims at once. Wait a minute and try again.')},ctx);
        if(claimed)return claimed instanceof Response?claimed:json(claimed);
        const formed=await formsRoute(env,request,path,q,formTools,ctx,me.id);
        if(formed)return formed instanceof Response?formed:json(formed);
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path.startsWith('/api/admin/')){
        if(!(await viewer(env,request,true))?.admin)throw new Problem(403,'Only an admin can see this.');
        if(path==='/api/admin/reviewers')return json(await reviewers(env,q));
        if(path==='/api/admin/submissions')return json(await submissions(env,q,await reviewerActors(env,Object.fromEntries(q),{text})));
        throw new Problem(404,'Unknown endpoint.');
      }
      const picture=path.match(AVATAR_PATH);
      if(picture)return await avatar(env,picture[1],picture[2]);
      if(path==='/api/account/connections'){const me=await viewer(env,request,true);if(!me)throw new Problem(401,'Sign in to see your account.');return json(await connections(env,me.id))}
      if(path==='/api/favourites'||path==='/api/favourites/crops'){
        // A browser that has never signed in has starred nothing, and reading starts no session.
        const me=await viewer(env,request);
        if(path==='/api/favourites')return json(me?await favouriteIds(env,me.id):{ids:[]},200,{'cache-control':'private, no-store'});
        return json(me?await favouriteCrops(env,me.id,integer(q,'offset',0),integer(q,'limit',60,60),listingItems):{items:[],total:0,offset:0,next:null},200,{'cache-control':'private, no-store'});
      }
      if(path==='/api/account')return json({user:await viewer(env,request,true),providers:providers(env)});
      if(path==='/health')return json(await browse.health(env));
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image){
        try{const found=await mediaResponse(env,request,image[1],ctx);if(found)return found}
        catch(error){if(error instanceof MediaIncomplete)throw new Problem(503,'Image publication is incomplete.');throw error}
        throw new Problem(404,'Image not found.');
      }
      // A round leaves out what its reviewer skipped lately, so it reads who is asking.
      if(path==='/atlas')return json(await browse.catalogue(env,ctx,url,q.get('purpose')==='review'?(await viewer(env,request))?.id??null:null));
      if(path==='/api/ranking')return await ranking(env,url,ctx);
      if(path==='/atlas/history')return json(await history(env,q,(await viewer(env,request))?.id??null,{text,integer}));
      const run=await runsRoute(env,ctx,url);
      if(run)return run;
      if(path==='/atlas/corpus/characters')return json(await browse.corpusCharacters(env,ctx,url),200,{'cache-control':'private, max-age=300'});
      const cropped=await cropRoute(env,ctx,url);
      if(cropped)return cropped;
      if(path==='/atlas/collection/status')return json(await browse.siteValue(env,'collection'));
      if(path==='/atlas/dates/stats')return json(await browse.dateStats(env,ctx,url));
      const read=await claimRead(env,url,claimTools,ctx);
      if(read)return read instanceof Response?read:json(read);
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviews(env,q.get('include_processed')==='true'),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const cited=path.match(/^\/atlas\/cite\/([^/]+)\/([^/]+)$/);
      if(cited&&(cited[1]==='grapheme'||cited[1]==='form'))return await citeCharacter(env,ctx,url,cited[1],cited[2],characterTools,OPEN);
      if(path==='/atlas/similar/model')return json(await modelInfo(env));
      if(path.startsWith('/atlas/ids/')){
        const drawing=await drawingFile(env,path.slice('/atlas/ids/'.length),request,ctx);
        if(!drawing)throw new Problem(404,'No drawing of this sequence.');
        return drawing;
      }
      if(path.startsWith('/atlas/similar/files/')){
        const file=await modelFile(env,path.slice('/atlas/similar/files/'.length),request,ctx);
        if(!file)throw new Problem(404,'No such model file.');
        return file;
      }
      const similar=path.match(/^\/atlas\/characters\/([^/]+)\/similar$/);
      if(similar){const named=await cropParam(env,similar[1],ctx);
        if(named&&'moved' in named)return movedPathResponse(url,similar[1],named.moved);
        const crop=named&&('id' in named?named.id:named.retired);
        return json(await similarCrops(env,crop,integer(q,'limit',12,20),listingItems,ctx))}

      if(path==='/layers/word-choices')return json({items:await wordChoices(env,text(q.get('q')??'',128,'reading')??'',[...(q.get('c')??'')].slice(0,4))});
      if(path==='/layers/suggest')return json(await suggest(env,q));
      if(path==='/layers/structure')return json(await structureOf(env,ctx,url.origin,q.get('c')||''));
      if(path==='/layers/search'){const found=await suggest(env,q);return json({...found,results:found.items,match:found.items[0]||null})}
      const layer=path.match(/^\/layers\/characters\/([^/]+)$/);
      if(layer)return json(await characterPage(env,ctx,url,decodeURIComponent(layer[1]),characterTools));
      if(path==='/layers/occurrences')return json(await occurrences(env,await characterCard(env,q.get('code_point')||'',characterTools,ctx),q));
      if(path==='/layers/candidates'){const found=await corpusOccurrences(env,await characterCard(env,q.get('code_point')||'',characterTools,ctx),q);
        return json({...found,glyphs:found.total,glyph_items:found.items,retry:false})}
      if(path==='/layers/gallery')return json(await browse.gallery(env,q));
      if(path==='/layers/decades')return json(await decades(env,ctx,url,characterTools));
      if(path==='/layers/chronology')return json(await chronology(env,ctx,url,characterTools));
      if(path==='/layers/summary')return json(await browse.siteValue(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures')return json(await cards(env,path.endsWith('ligatures')?'ligatures':'graphemes',q,ctx));
      if(path==='/atlas/corpus/reviews')return json(await flaggedCorpus(env,listingItems));
      if(path.startsWith('/atlas/forms/')){const formed=await formsRoute(env,request,path,q,formTools,ctx);
        if(formed)return formed instanceof Response?formed:json(formed)}
      throw new Problem(404,'Unknown endpoint.');
    }catch(error){
      const open=opened(path)?OPEN:{};
      if(error instanceof StorageUnavailable)console.error(JSON.stringify({event:'storage_unavailable',path,where:error.where,...described(error.cause)}));
      if(error instanceof Problem)return json({detail:error.message,...error.extra},error.status,
        error.extra.code==='busy'?{...open,'retry-after':String(RETRY_AFTER)}:open);
      throw error;
    }
  },
};

// A writer reads the catalogue as it now stands: a write that succeeds marks the browser for
// `CATALOGUE_LAG` seconds, and a marked request reads the stamps afresh, which the isolate then keeps.
const WROTE = 'atlas-wrote';
export const wroteLately = (cookie: string | null) => (cookie ?? '').split(';').some(part => part.trim() === `${WROTE}=1`);
export function markWriter(response: Response, lag: number): Response {
  if (!(lag > 0) || !response.ok) return response;
  const marked = new Response(response.body, response);
  marked.headers.append('set-cookie', `${WROTE}=1; Max-Age=${lag}; Path=/; HttpOnly; Secure; SameSite=Lax`);
  return marked;
}
export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    // A read is tried again while D1 is busy; a write is answered at once, and the browser keeps it
    // until the database is back.
    const read=request.method==='GET'||request.method==='HEAD';
    const scoped=wroteLately(request.headers.get('cookie'))?{...env,CATALOGUE_LAG:'0' as string} as Env:env;
    const outcome=await retried(()=>routes.fetch(request,scoped,ctx),read?READ_BUDGET:0);
    if('value' in outcome)return read?outcome.value:markWriter(outcome.value,Number(env.CATALOGUE_LAG));
    const path=new URL(request.url).pathname,open=opened(path)?OPEN:{};
    const busy=transient(outcome.error);
    console.error(JSON.stringify({event:'request_failed',path,method:request.method,busy,attempts:outcome.attempts,...described(outcome.error)}));
    if(busy)return json({detail:'The database is updating. Try again in a moment.',code:'busy'},503,{...open,'retry-after':String(RETRY_AFTER)});
    return json({detail:'The request could not be completed.',code:'failed'},500,open);
  },
} satisfies ExportedHandler<Env>;
