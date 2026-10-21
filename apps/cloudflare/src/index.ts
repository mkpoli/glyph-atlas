// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
import { ROUND_MAX } from './rounds';
import { held } from './held';
import { body, canonical, compose, graphemeKey, graphemeText, integer, literal, single, text } from './input';
export { canonical, compose, literal, single } from './input';
import { RECORD_MAX, readRecords, recordKey, type RecordPointer } from './records';
export { recordRanges } from './records';
import { formsRoute, withForm, formed, FORM_COLUMNS, type FormTools, type UnitForm } from './forms';
import { similarCrops } from './similar';
import { drawingFile } from './drawings';
import { MAX_BODY, QueryError, addressKey, boundedText, modelFile, modelInfo, query as imageQuery } from './reverse';
import { formsFor, withForms } from './cropForms';
import { structureOf, suggest, wordChoices } from './routes/search';
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
import { datingOf, documentOf } from './dating';
import { cards, characterCard, characterPage, chronology, byCodePoint, citeCharacter, corpusOccurrences, decades, describedCard, jamoCard, occurrences, type CharacterTools } from './routes/character';
export { leastTypicalQuery } from './forms';
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
// The pair and trigram frequencies Explore's grid shows: runs of crops and of corpus glyphs that follow
// each other on a line (`unit_ngrams`), counted by their graphemes (0071), most frequent first, so ん𛁅 and
// ん𛁈 are counted together as んし, with how many written forms each gathers. The whole site's count and a
// book's are each the first page of the counts the triggers keep (`ngram_counts`, 0074). The edge keeps
// one copy per catalogue version, length and book. A run is shown down the page where most of its
// occurrences are written that way.
const NGRAMS_MAX = 480, NGRAM_SIZES = new Set(['2', '3']);
function ngramSize(value: string) {
  if (!NGRAM_SIZES.has(value)) throw new Problem(404, 'Runs of two or three characters are counted.');
  return Number(value);
}
export function ngramsQuery() {
  return `SELECT graphemes AS text,n,2*down>=n AS vertical,forms FROM ngram_counts WHERE scope=? AND size=? ORDER BY n DESC,graphemes LIMIT ${NGRAMS_MAX}`;
}
async function ngrams(env: Env, ctx: ExecutionContext, url: URL, size: number) {
  const document = text(url.searchParams.get('document'), 256, 'document');
  const key = new Request(`${url.origin}/atlas/ngrams/${size}?document=${encodeURIComponent(document ?? '')}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const rows = await env.DB.prepare(ngramsQuery()).bind(document ?? '', size).all<{ text: string; n: number; vertical: number; forms: number }>();
  const body = { items: rows.results.map(row => ({ ...row, vertical: Boolean(row.vertical) })), limit: NGRAMS_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
// The page around a run, from one of its crops' context renders: the box holding every crop, with a
// margin of a fifth of the largest, clipped to the render. A render shows the page a few characters
// around its own crop, so it nearly always holds the whole run; the smallest that does is the sharpest.
// Null when a crop has no box or no render holds them all: the run's crops are then laid out apart.
type Rect = { x: number; y: number; w: number; h: number };
// A context render cut by its page's IIIF image service (`source.image_service`, a CODH page) is in that
// service's pixels, so the service can cut a run's region alone: a card then loads its run rather than the
// whole render, a tenth or less of it. The cut is asked for by width, as the published renders are, and no
// larger than CUT_WIDTH × CUT_HEIGHT or the region itself, as a service need not enlarge.
const CUT_WIDTH = 660, CUT_HEIGHT = 600;
export function runCut(service: string | null | undefined, render: string, region: Rect): string | null {
  if (!service || !/^https?:\/\//i.test(service) || !render.startsWith(service + '/')) return null;
  const scale = Math.min(1, CUT_WIDTH / region.w, CUT_HEIGHT / region.h);
  return `${service}/${region.x},${region.y},${region.w},${region.h}/${Math.max(1, Math.round(region.w * scale))},/0/default.jpg`;
}
export function runPage(crops: Json[]): { image: string; box: Rect; region: Rect; cut: string | null } | null {
  const boxes = crops.map(c => c.crop_box as Rect | null);
  if (boxes.some(b => !b)) return null;
  const left = Math.min(...boxes.map(b => b!.x)), top = Math.min(...boxes.map(b => b!.y));
  const right = Math.max(...boxes.map(b => b!.x + b!.w)), bottom = Math.max(...boxes.map(b => b!.y + b!.h));
  const holds = (r: Rect) => r.x <= left && r.y <= top && r.x + r.w >= right && r.y + r.h >= bottom;
  const render = crops.filter(c => c.context_image && c.context_box && holds(c.context_box))
    .sort((a, b) => a.context_box.w * a.context_box.h - b.context_box.w * b.context_box.h)[0];
  if (!render) return null;
  const box = render.context_box as Rect, margin = Math.max(...boxes.flatMap(b => [b!.w, b!.h])) / 5;
  const x = Math.max(box.x, left - margin), y = Math.max(box.y, top - margin);
  // Whole pixels, so the region cut, the region drawn and the view onto it are one rectangle.
  const rx = Math.floor(x), ry = Math.floor(y);
  const region = { x: rx, y: ry, w: Math.ceil(Math.min(box.x + box.w, right + margin)) - rx, h: Math.ceil(Math.min(box.y + box.h, bottom + margin)) - ry };
  return { image: render.context_image, box, region, cut: runCut(render.source?.image_service, render.context_image, region) };
}
// One run's occurrences: every place its characters follow each other on a line, as their crops in
// reading order, each with its box on the page, whether their line is written down the page, and the page
// around them (`runPage`); the run as a whole is written the way most of its occurrences are. The text is
// split into graphemes, one crop each, as a label holds one, and each is folded to its grapheme
// (`runGraphemes`), so a run is found by its graphemes (0071) whichever form each crop is written in; the
// crops shown are as written. A run of two or three is one row of `unit_ngrams`. A longer one starts from
// one of its trigrams, the rarest (`runProbeQuery`), and reaches the rest of its crops a pair at a time:
// publication writes a pair only where two crops follow each other and stand near, so a chain of pairs
// holds the runs a longer row would. The first row is read along the index of its graphemes (or its book
// and graphemes), so occurrences come in that index's order: how its first crop's letterforms were made
// (shaped by hand first, 0062), then that crop's shuffle and id. Every other row is found by its key,
// rightwards by the primary key and leftwards through `unit_ngram_second` (the length kept off its index,
// `+`, or the planner reads every pair), and every member by its id: its `units` row and its
// `corpus_units` row, either of which may be missing. A member stands on the site as a local crop or as a
// corpus glyph, whose record is its `units` row once a round named it and its published record (R2)
// before. Left joins keep the join order. Counting stops past RUN_COUNT_MAX, which is as deep as a run
// pages, and the first page alone counts. A page holds up to RUN_PAGE_CROPS crops, so a longer run comes
// in fewer occurrences at a time. RUN_MAX keeps a run inside one context render: a render reaches five
// character sizes along a column (`CONTEXT_REACH`), so its middle crop's holds about eleven, and
// `runPage` finds the run in it. A run of two or three narrows to one of the written forms its graphemes
// gather (`form`), which its first page lists (`runFormsQuery`).
// How a run's letterforms were made, as `hand_order` (0062) numbers its first member: shaped by hand
// (handwritten, inscribed, cut or drawn for the page, or written in a running or cursive hand), not
// known, and set from type. A run's page lists them in this order and narrows to one (`hand`).
export const HAND_ORDER: Record<string, number> = { hand: 0, unknown: 1, type: 2 };
const HAND_NAMES = Object.keys(HAND_ORDER);
function handGroup(q: URLSearchParams): number | null {
  const value = q.get('hand');
  if (!value || value === 'all') return null;
  if (!Object.hasOwn(HAND_ORDER, value)) throw new Problem(422, 'Invalid group.');
  return HAND_ORDER[value];
}
const RUN_FORMS_MAX = 24, RUN_MAX = 8, RUN_PAGE_CROPS = 192, RUN_COUNT_MAX = 2000, RUN_PROBE_MAX = 5000, RUN_WORKS_MAX = 60, RUN_NEAR_MAX = 12;
const NGRAM_COLUMNS = ['first', 'second', 'third'];
const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' });
/** The characters of a run, one crop each. */
export const runCharacters = (value: string) => [...graphemes.segment(value)].map(s => s.segment);
/** How a run of `size` crops is read when its row `a` starts at crop `anchor`: the joins after it, and the
 *  first crop of each pair they bind a text to, in the order they are bound. */
export function runFrom(size: number, anchor: number) {
  const span = Math.min(size, 3), crops: string[] = [], joins: string[] = [], links: number[] = [];
  for (let i = 0; i < span; i++) crops[anchor + i] = `a.${NGRAM_COLUMNS[i]}`;
  for (let i = anchor + span - 1; i < size - 1; i++) {
    joins.push(`CROSS JOIN unit_ngrams r${i} ON r${i}.first=${crops[i]} AND r${i}.size=2 AND +r${i}.graphemes=?`);
    crops[i + 1] = `r${i}.second`; links.push(i);
  }
  for (let i = anchor - 1; i >= 0; i--) {
    joins.push(`CROSS JOIN unit_ngrams l${i} ON l${i}.second=${crops[i + 1]} AND +l${i}.size=2 AND +l${i}.graphemes=?`);
    crops[i] = `l${i}.first`; links.push(i);
  }
  const units = crops.map((crop, i) => `LEFT JOIN units u${i} ON u${i}.id=${crop} LEFT JOIN corpus_units k${i} ON k${i}.id=${crop}`);
  return { from: ['FROM unit_ngrams a', ...joins, ...units].join(' '), links };
}
// A written form (`form`) narrows a run of two or three to the rows of that text, tested on each row the
// run's graphemes index reads: an index led by the text as well would cost a write and about 80 bytes
// for every run, to save reads on a rare form's page, which the edge then keeps.
const runWhere = (document: boolean, size: number, hand = false, form = false) => `${document ? 'a.document=? AND ' : ''}a.size=? AND a.graphemes=?`
  + `${form ? ' AND a.text=?' : ''}${hand ? ' AND a.hand_order=?' : ''}`
  + Array.from({ length: size }, (_, i) => ` AND (u${i}.origin='local' OR k${i}.id IS NOT NULL)`).join('');
// A group of how the letterforms were made (`hand`, the first row's `hand_order`, 0062) and a book
// (`document`) narrow the run through the index that leads with what they name; `sort=source` lists a
// run by book (0059) instead.
export function runOccurrencesQuery(size: number, anchor: number, document: boolean, hand = false, sort: 'hand' | 'source' = 'hand', form = false) {
  const members = Array.from({ length: size }, (_, i) => `u${i}.data AS c${i},k${i}.object AS o${i},k${i}.offset AS f${i},k${i}.size AS s${i}`);
  return `SELECT ${members.join(',')}, a.document AS document, a.vertical
    ${runFrom(size, anchor).from} WHERE ${runWhere(document, size, hand, form)}
    ORDER BY ${sort === 'source' && !document ? 'a.document,a.first' : 'a.hand_order,a.shuffle,a.first'} LIMIT ? OFFSET ?`;
}
// A run is counted a group at a time, each count stopping past RUN_COUNT_MAX: the whole run is their sum,
// which passes RUN_COUNT_MAX exactly when the run does.
export function runCountQuery(size: number, anchor: number, document: boolean, form = false) {
  return `SELECT count(*) AS n, sum(v) AS vertical FROM (SELECT a.vertical AS v ${runFrom(size, anchor).from}
    WHERE ${runWhere(document, size, true, form)} LIMIT ${RUN_COUNT_MAX + 1})`;
}
/** The books a run is in, with their occurrences (counted among the first RUN_COUNT_MAX) and one crop of each to name them by. */
export function runWorksQuery(size: number, anchor: number, form = false) {
  return `SELECT d AS document, count(*) AS n, min(f) AS sample FROM (SELECT a.document AS d, a.first AS f ${runFrom(size, anchor).from}
    WHERE ${runWhere(false, size, false, form)} AND a.document IS NOT NULL LIMIT ${RUN_COUNT_MAX + 1}) GROUP BY d ORDER BY n DESC,d LIMIT ${RUN_WORKS_MAX}`;
}
// The records of a page's occurrences: each member's row where it has one, else its published record,
// read with the page's other records (`corpusRecords`). An occurrence with a record that cannot be read
// is left off the page, as a listing leaves out a glyph whose record it cannot read.
type RunRow = { document: string | null; vertical: number } & Record<`c${number}`, string | null> & Record<`o${number}`, string | null>
  & Record<`f${number}` | `s${number}`, number | null>;
async function runRecords(env: Env, rows: RunRow[], size: number): Promise<(Json[] | null)[]> {
  const pointer = (row: RunRow, i: number) => ({ object: row[`o${i}`]!, offset: row[`f${i}`]!, size: row[`s${i}`]! });
  const read = await corpusRecords(env, rows.flatMap(row => Array.from({ length: size }, (_, i) => i)
    .filter(i => row[`c${i}`] === null && row[`o${i}`] !== null).map(i => pointer(row, i))));
  return rows.map(row => {
    const members = Array.from({ length: size }, (_, i) => row[`c${i}`] !== null ? parse(row[`c${i}`]!) : row[`o${i}`] !== null ? read.get(recordKey(pointer(row, i))) : undefined);
    return members.every(Boolean) ? members as Json[] : null;
  });
}
// A query is folded as a run's graphemes are (0071): each character to its grapheme's head, through the
// table of characters the site knows (`character_text`); one the table does not know stands for itself.
export const runGraphemesQuery = () => `SELECT c.character AS character, h.character AS head FROM characters c
  JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point') WHERE c.character IN (SELECT value FROM json_each(?))`;
async function runGraphemes(env: Env, parts: string[]): Promise<string[]> {
  const heads = new Map((await env.DB.prepare(runGraphemesQuery()).bind(JSON.stringify(parts)).all<{ character: string; head: string }>())
    .results.map(row => [row.character, row.head]));
  return parts.map(part => heads.get(part) ?? part);
}
// The written forms a run of two or three gathers, each with its count, most frequent first: the site's
// are one key range of `ngram_forms` (0074) in the order of `ngram_form_rank`; a book's are grouped from
// a capped read of the book's runs of those graphemes, as its books are (`runWorksQuery`).
export const runFormsQuery = (document: boolean) => document
  ? `SELECT text, count(*) AS n FROM (SELECT text FROM unit_ngrams WHERE document=? AND size=? AND graphemes=? LIMIT ${RUN_COUNT_MAX + 1})
    GROUP BY text ORDER BY n DESC,text LIMIT ${RUN_FORMS_MAX}`
  : `SELECT text,n FROM ngram_forms WHERE size=? AND graphemes=? ORDER BY n DESC,text LIMIT ${RUN_FORMS_MAX}`;
/** How many rows of a trigram there are, counting no further than RUN_PROBE_MAX. */
export function runProbeQuery(document: boolean) {
  return `SELECT count(*) AS n FROM (SELECT 1 FROM unit_ngrams WHERE ${document ? 'document=? AND ' : ''}size=3 AND graphemes=? LIMIT ${RUN_PROBE_MAX})`;
}
// How long each step of an answer took, as a `Server-Timing` header: a step's time runs from the previous
// step's end (`step`), or from the start for steps that run side by side (`beside`). A Worker's clock moves
// only across I/O, which is what the steps wait on.
export class Timing {
  private start = Date.now();
  private last = this.start;
  private steps: [string, number][] = [];
  step(name: string) { const now = Date.now(); this.steps.push([name, now - this.last]); this.last = now; }
  beside<T>(name: string, work: Promise<T>): Promise<T> {
    const from = Date.now();
    return work.finally(() => { this.steps.push([name, Date.now() - from]); });
  }
  header() { return [...this.steps, ['total', Date.now() - this.start] as [string, number]].map(([name, ms]) => `${name};dur=${ms}`).join(', '); }
}
async function runOccurrences(env: Env, ctx: ExecutionContext, url: URL, timing: Timing) {
  const q = url.searchParams;
  const value = text(q.get('text'), 96, 'text', true)!;
  const parts = runCharacters(value);
  if (parts.length < 2 || parts.length > RUN_MAX) throw new Problem(422, `A run is two to ${RUN_MAX} characters.`);
  const document = text(q.get('document'), 256, 'document');
  const most = Math.floor(RUN_PAGE_CROPS / parts.length);
  const offset = integer(q, 'offset', 0), group = handGroup(q);
  const sort = q.get('sort') === 'source' ? 'source' : q.get('sort') && q.get('sort') !== 'hand' ? null : 'hand';
  if (!sort) throw new Problem(422, 'Invalid sort.');
  // A written form of the run: as many characters, and a run of two or three, whose rows hold its text.
  const form = text(q.get('form'), 96, 'form');
  if (form && (parts.length > 3 || runCharacters(form).length !== parts.length)) throw new Problem(422, 'Invalid form.');
  // A page ends where the count does.
  if (offset >= RUN_COUNT_MAX) throw new Problem(404, 'A run does not page this far.');
  const limit = Math.min(integer(q, 'limit', Math.min(48, most), most), RUN_COUNT_MAX - offset);
  const version = await catalogueVersion(env);
  timing.step('version');
  const key = new Request(`${url.origin}/atlas/runs?${new URLSearchParams({ text: value, document: document ?? '', form: form ?? '', hand: String(group ?? ''), sort, limit: String(limit), offset: String(offset), v: version })}`);
  const cached = await caches.default.match(key);
  timing.step(cached ? 'cache-hit' : 'cache-miss');
  if (cached) return await cached.json() as Json;
  const size = parts.length, scope = document ? [document] : [], folded = await runGraphemes(env, parts);
  timing.step('graphemes');
  const span = (from: number, length: number) => folded.slice(from, from + length).join('');
  let anchor = 0;
  if (size > 3) {
    const probes = await env.DB.batch(Array.from({ length: size - 2 }, (_, i) =>
      env.DB.prepare(runProbeQuery(Boolean(document))).bind(...scope, span(i, 3)))) as D1Result<{ n: number }>[];
    const counts = probes.map(probe => probe.results[0].n);
    anchor = counts.indexOf(Math.min(...counts));
    timing.step('probe');
  }
  const { links } = runFrom(size, anchor);
  const written = form ? [form] : [];
  const bound = [...links.map(i => span(i, 2)), ...scope, Math.min(size, 3), span(anchor, Math.min(size, 3)), ...written];
  const styled = (n: number) => [...bound, n];
  const occurrences = env.DB.prepare(runOccurrencesQuery(size, anchor, Boolean(document), group !== null, sort, Boolean(form))).bind(...(group === null ? bound : styled(group)), limit, offset);
  // The first page also counts each group, which counts the run as a whole or in the group chosen, names
  // its books, and lists the written forms a run of two or three gathers, whichever form is chosen.
  const first = offset ? [] : [
    ...HAND_NAMES.map(name => env.DB.prepare(runCountQuery(size, anchor, Boolean(document), Boolean(form))).bind(...styled(HAND_ORDER[name]))),
    env.DB.prepare(runWorksQuery(size, anchor, Boolean(form))).bind(...links.map(i => span(i, 2)), Math.min(size, 3), span(anchor, Math.min(size, 3)), ...written),
    size <= 3 ? env.DB.prepare(runFormsQuery(Boolean(document))).bind(...scope, size, span(0, size)) : env.DB.prepare('SELECT 1 WHERE 0'),
  ];
  const [page, ...rest] = await env.DB.batch([occurrences, ...first]) as D1Result<any>[];
  timing.step('rows');
  const found = page.results as RunRow[];
  // The crops of one run stand on one page, so they share their document's dates. The books' titles are
  // read beside the page's records rather than after them.
  // The forms are the batch's last statement and the books the one before it.
  const listed = rest.length ? rest.pop()!.results as { text: string; n: number }[] : null, forms = size <= 3 ? listed : null;
  const books = rest.length ? rest.pop()!.results as { document: string; n: number; sample: string }[] : null;
  const [dating, records, works] = await Promise.all([timing.beside('dating', datingOf(env, found.map(row => row.document))),
    timing.beside('records', runRecords(env, found, size)), books ? timing.beside('works', runWorks(env, books)) : null]);
  timing.step('reads');
  const items = found.flatMap((row, r) => {
    const crops = records[r];
    if (!crops) return [];
    const dated = row.document ? dating.get(row.document) : undefined;
    // An occurrence transcribed on みんなで翻刻 links to its page there, as a crop's inspector does: the
    // first of its crops that names one.
    const honkoku = crops.map(c => honkokuPage(c, row.document ?? c.source?.document_id ?? null)).find(Boolean) ?? null;
    return { crops: crops.map(c => ({ ...listing(c), crop_box: c.crop_box ?? null, dating: dated ?? {} })),
      vertical: Boolean(row.vertical), page: runPage(crops), honkoku_url: honkoku };
  });
  const hands = rest.map(result => result.results[0] as { n: number; vertical: number | null });
  const chosen = group === null ? hands : hands.slice(group, group + 1);
  const counted = hands.length ? { n: chosen.reduce((sum, h) => sum + h.n, 0), vertical: chosen.reduce((sum, h) => sum + (h.vertical ?? 0), 0) } : undefined;
  const body = { text: value, size, document, form, hand: group === null ? 'all' : HAND_NAMES[group], sort, next_offset: offset + found.length, items,
    ...(counted && { total: Math.min(counted.n, RUN_COUNT_MAX), more: counted.n > RUN_COUNT_MAX, vertical: 2 * (counted.vertical ?? 0) >= counted.n,
      hands: Object.fromEntries(HAND_NAMES.map((name, i) => [name, Math.min(hands[i].n, RUN_COUNT_MAX)])), hand_groups: HAND_NAMES, works, forms }) };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
// A book's name is its crops' source title, read from one of its crops by key, or the title its corpus
// publication gave the document (`corpus_documents`, 0075): both in one batch, by primary key.
async function runWorks(env: Env, rows: { document: string; n: number; sample: string }[]) {
  if (!rows.length) return [];
  const [crops, documents] = await env.DB.batch([
    env.DB.prepare(`SELECT id,json_extract(data,'$.source') AS title FROM units WHERE id IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(rows.map(row => row.sample))),
    env.DB.prepare(`SELECT document,title FROM corpus_documents WHERE document IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(rows.map(row => row.document))),
  ]) as [D1Result<{ id: string; title: string | null }>, D1Result<{ document: string; title: string | null }>];
  const named = new Map(crops.results.map(row => [row.id, typeof row.title === 'string' && row.title.startsWith('{') ? parse(row.title).title ?? null : row.title]));
  const published = new Map(documents.results.map(row => [row.document, row.title]));
  return rows.map(row => ({ id: row.document, title: named.get(row.sample) ?? published.get(row.document) ?? null, count: row.n }));
}
// The runs near one, to move between them: the shorter runs a trigram or four holds, the trigrams a pair
// begins, and the runs of the same length that begin with the same characters (a pair's first, a
// trigram's first two), most frequent first. The run is folded to its graphemes as its occurrences are
// (`runGraphemes`), and a prefix is one key range of the site's counts by graphemes (`ngram_counts`,
// 0074), a row per grapheme sequence rather than one per occurrence; the answer is kept at the edge per
// catalogue version like the counts.
export const runRangeQuery = () => `SELECT graphemes AS text,n,2*down>=n AS vertical,forms FROM ngram_counts
  WHERE scope='' AND size=? AND graphemes>=? AND graphemes<? AND graphemes<>? ORDER BY n DESC,graphemes LIMIT ${RUN_NEAR_MAX}`;
export const runHasQuery = () => "SELECT graphemes AS text FROM ngram_counts WHERE scope='' AND size=? AND graphemes=?";
async function runRelated(env: Env, ctx: ExecutionContext, url: URL) {
  const value = text(url.searchParams.get('text'), 96, 'text', true)!;
  const parts = runCharacters(value);
  if (parts.length < 2 || parts.length > RUN_MAX) throw new Problem(422, `A run is two to ${RUN_MAX} characters.`);
  const key = new Request(`${url.origin}/atlas/runs/related?${new URLSearchParams({ text: value, v: await catalogueVersion(env) })}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const size = parts.length, folded = await runGraphemes(env, parts), join = (from: number, to: number) => folded.slice(from, to).join('');
  const beyond = (prefix: string) => prefix + '\u{10FFFF}';
  const near = (length: number, prefix: string, except = '') => env.DB.prepare(runRangeQuery()).bind(length, prefix, beyond(prefix), except);
  const whole = join(0, size);
  const lead = size === 2 ? join(0, 1) : join(0, 2);
  const inside = size === 3 || size === 4 ? [join(0, size - 1), join(1, size)] : [];
  const [longer, siblings, ...held] = await env.DB.batch([
    size === 2 ? near(3, whole) : env.DB.prepare('SELECT 1 WHERE 0'),
    size === 2 || size === 3 ? near(size, lead, whole) : env.DB.prepare('SELECT 1 WHERE 0'),
    ...inside.map(part => env.DB.prepare(runHasQuery()).bind(size - 1, part)),
  ]) as D1Result<any>[];
  const shown = (rows: Json[]) => rows.map(row => ({ ...row, vertical: Boolean(row.vertical) }));
  const body = { text: value, size, lead, inside: held.flatMap(result => result.results.map((row: Json) => row.text as string)),
    longer: size === 2 ? shown(longer.results) : [], siblings: size === 2 || size === 3 ? shown(siblings.results) : [], limit: RUN_NEAR_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
function chunks<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
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
// A document's characters in source order. The primary key serves the filter and the order, and each
// row joins its unit by id: a published unit gives its current character, state and revision.
export const documentCharactersQuery = () => `SELECT c.unit,c.data,u.character,u.state,u.revision,json_extract(u.data,'$.issue') AS issue
  FROM document_characters c LEFT JOIN units u ON u.id=c.unit WHERE c.document=? ORDER BY c.ord`;
// Other sites read this listing from the browser, so it is served to any origin, errors included. The
// edge copy is keyed by the catalogue version, so any change to the units makes a new key; browsers
// revalidate every time.
const OPEN = { 'access-control-allow-origin': '*' };
// Reads any site may make: a document's characters and a citation.
const opened = (path: string) => path.startsWith('/atlas/documents/') || path.startsWith('/atlas/cite/');
async function documentCharacters(env: Env, url: URL, document: string, ctx: ExecutionContext) {
  const key = new Request(`${url.origin}${url.pathname}?v=${encodeURIComponent(await catalogueVersion(env))}`);
  const served = (body: BodyInit | null) => new Response(body, { headers: { 'content-type': 'application/json',
    'cache-control': 'no-cache', 'x-content-type-options': 'nosniff', ...OPEN } });
  const cached = await caches.default.match(key);
  if (cached) return served(cached.body);
  const rows = await env.DB.prepare(documentCharactersQuery()).bind(document)
    .all<{ unit: string; data: string; character: string | null; state: string | null; revision: number | null; issue: string | null }>();
  if (!rows.results.length) throw new Problem(404, 'No characters are published for this document.');
  const characters = rows.results.map(r => {
    const d = parse(r.data);
    if (r.state === null) return { ...d, unit: r.unit, atlas: false };
    const label = r.character ?? d.label;
    // A label a reviewer changed or confirmed on the site is the review's, not its first source's.
    const source = label !== d.label || r.state === 'checked' ? 'review' : d.source;
    return { ...d, unit: r.unit, atlas: true, label, source, state: r.state, revision: r.revision, issue: r.issue };
  });
  const body = JSON.stringify({ document, published_at: await meta(env, 'published_at'), characters });
  ctx.waitUntil(caches.default.put(key, new Response(body, { headers: { 'content-type': 'application/json', 'cache-control': 'public, max-age=60' } })));
  return served(body);
}
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
        try{return json(await imageQuery(env,input,itemsFor))}
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
      const counted=path.match(/^\/atlas\/ngrams\/(\d+)$/);
      if(counted)return json(await ngrams(env,ctx,url,ngramSize(counted[1])));
      if(path==='/atlas/runs/related')return json(await runRelated(env,ctx,url));
      if(path==='/atlas/runs'){const timing=new Timing();const body=await runOccurrences(env,ctx,url,timing);return json(body,200,{'server-timing':timing.header()});}
      if(path==='/atlas/corpus/characters')return json(await browse.corpusCharacters(env,ctx,url),200,{'cache-control':'private, max-age=300'});
      const cropped=await cropRoute(env,ctx,url);
      if(cropped)return cropped;
      if(path==='/atlas/collection/status')return json(await browse.siteValue(env,'collection'));
      if(path==='/atlas/dates/stats')return json(await browse.dateStats(env,ctx,url));
      const document=path.match(/^\/atlas\/documents\/([^/]+)\/characters$/);
      if(document){let id:string;try{id=decodeURIComponent(document[1])}catch{throw new Problem(404,'No characters are published for this document.')}
        return await documentCharacters(env,url,id,ctx)}
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
      if(similar){let id:string;try{id=decodeURIComponent(similar[1])}catch{throw new Problem(404,'This character is not in the published collection.')}
        return json(await similarCrops(env,id,integer(q,'limit',12,20),itemsFor))}
      if(path==='/layers/word-choices')return json({items:await wordChoices(env,text(q.get('q')??'',128,'reading')??'',[...(q.get('c')??'')].slice(0,4))});
      if(path==='/layers/suggest')return json(await suggest(env,q,ctx));
      if(path==='/layers/structure')return json(await structureOf(env,ctx,url.origin,q.get('c')||''));
      if(path==='/layers/search'){const found=await suggest(env,q,ctx);return json({...found,results:found.items,match:found.items[0]||null})}
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
