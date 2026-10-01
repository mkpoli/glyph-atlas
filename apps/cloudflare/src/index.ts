// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
import { ROUND_MAX } from './rounds';
import { formsRoute, withForm, formed, FORM_COLUMNS, type FormTools, type UnitForm } from './forms';
import { similarCrops } from './similar';
import { componentSearch, componentTerm } from './components';
import { formProblem, type FormProblem } from './writtenForm';
export { leastTypicalQuery } from './forms';
export { componentMatchQuery } from './components';
type Json = Record<string, any>;
type UnitRow = { id: string; origin: string; character: string | null; state: string; revision: number;
  quiz: number; category?: string; data: string; snapshot: string; context: string; visual: string; style?: string;
  written_form?: string | null;
  // A corpus glyph nothing has named yet: it has no `units` row, and this is where it is published.
  fresh?: CorpusRow };
type CorpusRow = {id:string;character:string|null;family:string|null;visual_group:string|null;production:string;style:string;shuffle:number;object:string;offset:number;size:number};
class Problem extends Error {
  constructor(public status: number, message: string, public extra: Json = {}) { super(message) }
}
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const parse = (value: string): Json => JSON.parse(value);
const unavailable = { status: 'unavailable', candidates: [] };
// A label's category is its first character's script, as the migrations and publication scripts compute it.
// 구결자 have no Unicode script property, so they are tested by their Hanyang private-use range.
const isGugyeol=(value:string)=>{const c=value.codePointAt(0)??-1;return c>=0xF67E&&c<=0xF77C};
export const categoryOf=(value:string)=>{const first=[...value][0]??'';return /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(first)?'kana':/\p{Script=Han}/u.test(first)?'kanji':/\p{Script=Hangul}/u.test(first)?'hangul':isGugyeol(first)?'gugyeol':'other'};
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
// NFC composes a voiced kana, but it also maps each CJK compatibility ideograph to its unified twin,
// and those are characters of their own here; they are kept as written.
const COMPATIBILITY = /[\uF900-\uFAFF\u{2F800}-\u{2FA1F}]/u;
export function compose(value: string): string {
  let out = '', run = '';
  for (const c of value) { if (COMPATIBILITY.test(c)) { out += run.normalize('NFC') + c; run = '' } else run += c }
  return out + run.normalize('NFC');
}
export function literal(value: string): string {
  const trimmed = value.trim();
  if (/^(U\+[0-9a-f]{4,6})(\s+U\+[0-9a-f]{4,6})*$/i.test(trimmed)) {
    try { return compose(trimmed.split(/\s+/).map(v => String.fromCodePoint(parseInt(v.slice(2), 16))).join('')) }
    catch { throw new Problem(422, 'Invalid code point.') }
  }
  return compose(trimmed);
}
export const hira = (value: string) => [...literal(value)].map(c => {
  const n = c.codePointAt(0)!; return n >= 0x30a1 && n <= 0x30f6 ? String.fromCodePoint(n - 0x60) : c;
}).join('');
export const single = (value: string) => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(value)].length === 1;
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
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
// Whether a crop may be dealt in Quick review at all; its review state says whether it is due now.
// A local crop is withheld only by the alignment repair. A corpus glyph needs an image this site may
// serve and a written character, since a round asks whether the crop is that character.
const dealable=(origin:string,data:Json)=>origin==='corpus'?Boolean(data.proxyable&&data.written_character):data.repair?.quiz!==false;
const productionOf=(data:Json)=>typeof data.production==='string'?data.production:'unknown';
// The first round or review that names a corpus glyph writes its `units` row from the published record,
// in the same batch and before the rows that reference it.
function materialise(env:Env,row:UnitRow&{fresh:CorpusRow}){
  const d=parse(row.data);
  return env.DB.prepare('INSERT OR IGNORE INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
    .bind(row.id,'corpus',d.written_character||null,d.reading||null,d.grapheme||(d.written_character?cp(d.written_character):null),d.visual_group?.id||null,row.fresh.production,
      row.category||categoryOf(d.label),d.state,d.revision,row.quiz,1,row.fresh.shuffle,row.data,row.snapshot,row.context,row.visual,null,row.fresh.style,null);
}
async function corpusData(env:Env,row:CorpusRow):Promise<Json>{
  if(row.size>128*1024)throw new Problem(503,'Invalid published record.');
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object)throw new Problem(503,'The corpus publication is incomplete.');
  return withForm(env,await object.json<Json>(),formTools);
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
  const rest = ids.filter(id => !found.has(id));
  if (rest.length) {
    const pointers = await env.DB.prepare(`SELECT * FROM corpus_units WHERE id IN (${marks(rest.length)})`)
      .bind(...rest).all<CorpusRow>();
    const records = await Promise.all(pointers.results.map(p => corpusData(env, p).then(d => [p.id, d] as const, () => null)));
    for (const record of records) if (record) found.set(record[0], { ...listing(record[1]), origin: 'corpus' });
  }
  return found;
}
function compact(row: UnitRow): Json {
  return { ...listing(parse(row.data)), ...(row.style ? { style: row.style } : {}), ...(row.written_form ? { written_form: row.written_form } : {}) };
}
// A crop's record as its inspector reads it, with the written form its row holds (0038).
const record = (row: UnitRow): Json => ({ ...parse(row.data), written_form: row.written_form ?? null });
// A record as a listing shows it, without the fields only its inspector needs.
function listing(d: Json): Json {
  const { text, line, context_image, context_box, crop_box, ...rest } = d;
  return rest;
}
// Skips that still count: from a round that was not undone, at the crop's current box.
const SKIPS = `FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
  WHERE k.target=units.id AND k.box IS json_extract(units.data,'$.box')`;
// What rounds made of a crop at its current box (`unit_marks`): a pending crop two reviewers skipped is
// `hard` and leaves the rounds for its own list; one a round showed and left unflagged is `seen`, out
// of the queue with no decision.
const MARK = `(SELECT mark FROM unit_marks m WHERE m.id=units.id)`;
// A standing review from the character inspector (`character-review`), not a round's own verdict;
// an undone review stops counting.
export const reviewedInInspectorQuery = () => `EXISTS(SELECT 1 FROM events e JOIN submissions f ON f.id=e.submission AND f.undone=0
  WHERE e.target=units.id AND e.kind='review' AND json_extract(json_extract(e.event,'$.evidence'),'$.kind')='character-review')`;
const REVIEWED_IN_INSPECTOR = reviewedInInspectorQuery();
// The classifier's doubt about a crop's label, as `atlas review suspects` computed it, or null.
const SUSPECT = `(SELECT json_object('p',m.p,'reads_as',m.reads_as,'label',m.label,'box',json(m.box)) FROM unit_suspects m WHERE m.id=units.id)`;
type Suspect = { p: number; reads_as: string | null; label: string; box: Json | null };
// A mark holds while the crop keeps the label and box the classifier was shown; a crop relabelled or
// re-cut since is no longer what it judged.
export function suspectOf(mark: Suspect | null | undefined, item: Json): Json | null {
  if (!mark || mark.label !== item.label) return null;
  const a = mark.box, b = item.box ?? null;
  const same = a === null || b === null ? a === b : ['x', 'y', 'w', 'h'].every(k => Math.abs(Number(a[k]) - Number(b[k])) < 1e-6);
  return same ? { p: mark.p, reads_as: mark.reads_as } : null;
}
// The crops that can be flagged or hard: those stored as flagged (`unit_state`), and those marked hard
// (`unit_mark`). Every crop the effective state calls flagged or hard is among them.
export const attentionCandidatesQuery = () => `id IN (SELECT id FROM units WHERE origin='local' AND state='flagged'
  UNION SELECT id FROM unit_marks WHERE mark='hard')`;
const ATTENTION_CANDIDATES = attentionCandidatesQuery();
const EFFECTIVE_STATE = `iif(state='pending',coalesce(${MARK},state),state)`;
// How long a crop a reviewer skipped stays out of that reviewer's own rounds.
const SKIP_REST_MS = 3 * 24 * 60 * 60 * 1000;
const quoted = (value: string) => `'${value.replaceAll("'", "''")}'`;
const restSince = () => new Date(Date.now() - SKIP_REST_MS).toISOString();
// A reviewer's own skip that still rests: at the crop's current box, from a round not undone.
const OWN_SKIP = (reviewer: string, since: string) => `EXISTS(SELECT 1 ${SKIPS} AND k.actor=${quoted(reviewer)} AND k.at>${quoted(since)})`;
// The state as one reviewer sees it: a crop they skipped lately is `skipped` for them.
export function stateFor(reviewer: string | null, since = restSince()): string {
  if (!reviewer) return EFFECTIVE_STATE;
  // The reviewer's own recent skip is tested first: it is one indexed probe and false for most rows.
  return `iif(state='pending' AND ${OWN_SKIP(reviewer, since)} AND ${MARK} IS NULL,'skipped',${EFFECTIVE_STATE})`;
}
// What a shown crop's pixels are named by: a local crop's page hash, a corpus glyph's source revision.
const pixels = (crop: Json) => crop.image_sha256 ?? crop.source_revision;
// The crops a round names: flagged answers, and crops it showed and left unflagged. A round carries
// either or both; a single-crop review carries only its answer.
export function validRound(input: Json, target?: string): { answers: Json[]; seen: Json[]; skipped: Json[] } {
  const round = !target;
  if (!round && (input.seen !== undefined || input.skipped !== undefined)) throw new Problem(422, 'Only a round records seen or skipped crops.');
  const answers = round ? (input.answers ?? []) : [{ ...input, id: target }];
  const seen = round ? (input.seen ?? []) : [];
  const skipped = round ? (input.skipped ?? []) : [];
  if (!Array.isArray(answers) || !Array.isArray(seen) || !Array.isArray(skipped)) throw new Problem(422, `A round needs 1–${ROUND_MAX} distinct crops.`);
  const ids = [...answers, ...seen, ...skipped].map(crop => crop?.id);
  if (ids.length < 1 || ids.length > ROUND_MAX || new Set(ids).size !== ids.length) throw new Problem(422, `A round needs 1–${ROUND_MAX} distinct crops.`);
  for (const crop of [...seen, ...skipped]) {
    text(crop?.id, 512, 'character id', true);
    if (typeof pixels(crop) !== 'string' || !/^[a-f0-9]{64}$/.test(pixels(crop))) throw new Problem(422, 'Invalid image hash.');
    // A crop's image is compared with its record, never stored. Corpus glyphs link their source's own
    // file, and an HNG file name percent-encodes Japanese, so the address can run past 256 characters.
    if (crop.image !== undefined) text(crop.image, 2048, 'crop image', true);
  }
  return { answers, seen, skipped };
}
// A batch correction names one written character and the crops a reader selected as that character:
// each crop becomes a `wrong`/`character` answer, checked against its own revision and pixels.
export function validBatch(input: Json): { character: string; crops: Json[] } {
  const character = writtenCharacter(text(input.character, 32, 'character', true)!);
  const crops = input.crops;
  if (!Array.isArray(crops) || crops.length < 1 || crops.length > ROUND_MAX || new Set(crops.map(crop => crop?.id)).size !== crops.length)
    throw new Problem(422, `A correction needs 1–${ROUND_MAX} distinct crops.`);
  for (const crop of crops) text(crop?.id, 512, 'crop id', true);
  return { character, crops };
}
// A written character a reviewer names: one character, which may carry combining marks or a variation
// selector, and may be a private-use code point (구결자 are encoded there). Control, format, surrogate
// and separator code points are refused, as is a combining mark with no base.
export function writtenCharacter(value: string): string {
  const written = literal(value);
  if (!written || /[\p{Cc}\p{Cf}\p{Cs}\p{Z}]/u.test(written) || /^\p{M}/u.test(written) || !single(written))
    throw new Problem(422, 'Choose one written character.');
  return written;
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
// A production is a node of the tree in `data/vocab/production.yaml`, written as its path
// (`printed/type/wood`); `tests/test_production.py` holds this list to the file. A material scope is
// `all`, a node with everything under it, or `not:` and a node.
export const PRODUCTIONS = ['handwritten','inscribed','inscribed/stone','inscribed/metal','inscribed/bone','inscribed/wood','printed','printed/woodblock','printed/type','printed/type/wood','printed/type/ceramic','printed/type/metal','printed/type/metal/copper','printed/type/metal/iron','printed/type/metal/lead','printed/engraved','printed/lithograph','printed/stencil','printed/phototype','printed/digital','typewritten','mixed','unknown'];
// A Quick review round leaves movable type out unless asked: it fills whole books with near-identical glyphs.
const REVIEW_SCOPE = 'not:printed/type';
const validScope = (scope: string) => scope === 'all' || PRODUCTIONS.includes(scope.replace(/^not:/, ''));
const within = (value: string, node: string) => value === node || value.startsWith(node + '/');
// A material filter on a `production` column. A node and everything under it are one index range, from
// `node` up to `node0`: ids are lower-case letters and '/', and '0' sorts right after '/', so nothing
// else falls in it.
function material(scope: string, column: string): [string, string[]] {
  if (scope === 'all') return ['1=1', []];
  const negated = scope.startsWith('not:'), node = negated ? scope.slice(4) : scope;
  const test = `${column}>=? AND ${column}<?`;
  return [negated ? `NOT (${test})` : `(${test})`, [node, node + '0']];
}
const inMaterial = (scope: string, value: string) =>
  scope === 'all' || (scope.startsWith('not:') ? !within(value, scope.slice(4)) : within(value, scope));
// How far a round pages. Rounds are dealt from the front and a saved crop leaves the queue, so a
// reader never gets this deep; the bound keeps a crawler from reading a character's whole corpus.
const ROUND_OFFSET_MAX = 4096;
// Named corpus glyphs come back due after an undo, or when a skip's rest ends. Only a round of their
// own character deals them, from its most recently named pending rows, and the category counts leave
// them out: either way no request reads every named glyph. Should a character hold more named
// pending rows than this, mostly glyphs already seen, an older one that is due again waits outside it.
const NAMED_WINDOW = 256;
// `shuffle` is the first 28 bits of the id's SHA-256.
const SHUFFLE_RANGE = 268435456;
// What the cached listings are keyed by: every way `units` and the review tables change moves it. A
// publication stamps `published_at`, `refresh_published_units.py` stamps `units_refreshed_at`, a
// review or undo adds an event, a round adds a submission, and an undo of a round with no review
// only marks its submission undone.
async function catalogueVersion(env: Env): Promise<string> {
  const version = await env.DB.prepare(`SELECT (SELECT value FROM metadata WHERE key='published_at') AS published,
    (SELECT value FROM metadata WHERE key='units_refreshed_at') AS refreshed,
    (SELECT max(rowid) FROM events) AS event,(SELECT max(rowid) FROM submissions) AS submission,
    (SELECT count(*) FROM submissions WHERE undone=1) AS undone`)
    .first<{ published: string | null; refreshed: string | null; event: number | null; submission: number | null; undone: number }>();
  return [version?.published ?? '', version?.refreshed ?? '', version?.event ?? 0, version?.submission ?? 0, version?.undone ?? 0].join(':');
}
// Browse counts every local crop by character and state, which reads the whole table and takes
// seconds, and every visitor gets the same answer. The edge keeps one copy per catalogue version.
const FACETS_TTL = 3600;
// The grapheme a label is filed under: its family, or its own code points when it has none. The
// publication writes `family` from the same rule (`atlas.grapheme_of`).
export const graphemeOf = (label: string, family: string | null) => family || cp(label) || null;
type Facet = { label: string; family: string | null; document: string | null; title: string | null; state: string; n: number };
async function browseFacets(env: Env, ctx: ExecutionContext, url: URL, production: string, stored: D1PreparedStatement, marked: D1PreparedStatement) {
  const key = new Request(`${url.origin}/atlas/facets?by=character,family,document&production=${encodeURIComponent(production)}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Facet[];
  const [counted, marks] = await env.DB.batch<Facet>([stored, marked]);
  const groups = moved(counted.results, marks.results);
  ctx.waitUntil(caches.default.put(key, Response.json(groups, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return groups;
}
// Corpus glyphs by character in the material asked for, with the grapheme the character table files each
// under: what the grapheme browser counts beside the collection's own crops, so a character the site
// holds only as corpus glyphs (債) is listed too. It is its own request, which the browser makes when
// it opens, so catalogue pages carry none of it. `corpus_characters` is read whole (one row per
// character and material), and the edge keeps one copy per `corpus_counts_at`: every recount a
// publication runs (`CORPUS_REFRESH`, which a forms reload runs too) stamps it with the recount, and
// the forms drain stamps it once a decision has moved glyphs between characters. The browser keeps
// its copy for five minutes.
async function corpusVersion(env: Env) {
  return (await env.DB.prepare("SELECT value FROM metadata WHERE key='corpus_counts_at'").first<{ value: string }>())?.value ?? '';
}
export function browseCorpusQuery(production: string) {
  const [materials, values] = material(production, 'cc.production');
  return { sql: `SELECT cc.character AS label,sum(cc.n) AS n,json_extract(ch.data,'$.grapheme.code_point') AS family
    FROM corpus_characters cc LEFT JOIN characters ch ON ch.character=cc.character WHERE ${materials} GROUP BY cc.character`, values };
}
async function corpusCharacters(env: Env, ctx: ExecutionContext, url: URL) {
  const production = url.searchParams.get('production') || 'all';
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const key = new Request(`${url.origin}/atlas/corpus/characters?production=${encodeURIComponent(production)}&v=${encodeURIComponent(await corpusVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const { sql, values } = browseCorpusQuery(production);
  const rows = (await env.DB.prepare(sql).bind(...values).all<{ label: string; n: number; family: string | null }>()).results;
  // [label, grapheme, glyphs], one per character that has any.
  const body = { items: rows.filter(row => row.label && row.n > 0).map(row => [row.label, graphemeOf(row.label, row.family), row.n]) };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
// The crops a listing starts from: local ones, those a round may deal, in the material asked for.
// A round and its reference strips name their character, and read it through `unit_character`: the
// review filter is kept off its index (`+`), which would otherwise drive the query over every crop
// that can be dealt.
export function listingFilter(review: boolean, production: string, character: string | null) {
  const [materials, materialValues] = material(production, 'production');
  const where = ["origin='local'", ...(review ? [character === null ? 'quiz=1' : '+quiz=1'] : []), materials];
  const values: (string | number)[] = [...materialValues];
  if (character !== null) { where.push('character=?'); values.push(character) }
  return { where, values };
}
// Counts by character and state, and for browsing by book as well; a book's title is the one its
// crops were published with. A round asks only for its own character's: a review response that names
// a character carries that character's `categories` and `counts` alone.
// Every character's counts by stored state are read from `unit_counts`, which the triggers keep in
// step with `units`. The few pending crops rounds made something of (`unit_marks`), and those the
// reviewer skipped during the rest, are counted apart and moved out of `pending`: working out every
// crop's state as it is counted reads each crop twice. A named character's few crops work out their
// own state as they are counted.
export function facetsQueries(review: boolean, reviewer: string | null, where: string[], named: boolean, since = restSince()) {
  const filter = where.join(' AND '), book = review ? 'NULL' : 'document';
  if (named) return { stored: `SELECT character AS label,max(family) AS family,NULL AS document,NULL AS title,${stateFor(reviewer, since)} AS state,count(*) AS n
    FROM units WHERE ${filter} GROUP BY 1,5`, marked: null, skipped: null };
  // `unit_counts` keeps a missing key as ''.
  const stored = review
    ? `SELECT nullif(character,'') AS label,nullif(max(family),'') AS family,NULL AS document,NULL AS title,state,sum(n) AS n
    FROM unit_counts WHERE ${filter} GROUP BY character,state`
    : `SELECT nullif(character,'') AS label,nullif(max(family),'') AS family,nullif(document,'') AS document,nullif(max(title),'') AS title,state,sum(n) AS n
    FROM unit_counts WHERE ${filter} GROUP BY character,document,state`;
  // Every mark is read once, and its crop by id.
  const marked = `SELECT character AS label,${book} AS document,m.mark AS state,count(*) AS n
    FROM unit_marks m CROSS JOIN units ON units.id=m.id WHERE ${filter} AND state='pending' GROUP BY 1,2,3`;
  if (!reviewer) return { stored, marked, skipped: null };
  const skipped = `SELECT character AS label,${book} AS document,'skipped' AS state,count(*) AS n FROM units
    WHERE id IN (SELECT target FROM skips WHERE actor=${quoted(reviewer)} AND at>${quoted(since)}) AND ${filter}
    AND state='pending' AND ${MARK} IS NULL AND ${OWN_SKIP(reviewer, since)} GROUP BY 1,2`;
  return { stored, marked, skipped };
}
// Stored counts with the crops counted apart moved from `pending` to the state they are in. A
// reviewer's skips are counted now and browse's counts may be the edge's copy of this catalogue
// version, so a move never takes more than `pending` holds.
export function moved(stored: Facet[], moves: Facet[]): Facet[] {
  const key = (row: { label: string; document: string | null }, state: string) => JSON.stringify([row.label, row.document, state]);
  const rows = new Map(stored.map(row => [key(row, row.state), { ...row }]));
  for (const move of moves) {
    const pending = rows.get(key(move, 'pending'));
    const n = Math.min(move.n, pending?.n ?? 0);
    if (!pending || !n) continue;
    pending.n -= n;
    const target = rows.get(key(move, move.state)) ?? { ...pending, state: move.state, n: 0 };
    target.n += n;
    rows.set(key(move, move.state), target);
  }
  return [...rows.values()].filter(row => row.n > 0);
}
// The two-character frequencies Explore's grid shows: crops that follow each other on a line
// (`unit_pairs`), counted by the text their labels make, most frequent first. The whole collection's
// count reads every pair and a book's reads its own, through the index that also groups them; the edge
// keeps one copy per catalogue version and book.
const PAIRS_MAX = 480;
export function pairsQuery(document: boolean) {
  return `SELECT text,count(*) AS n FROM unit_pairs
    WHERE ${document ? 'document=? AND ' : ''}text IS NOT NULL GROUP BY text ORDER BY n DESC,text LIMIT ${PAIRS_MAX}`;
}
async function pairs(env: Env, ctx: ExecutionContext, url: URL) {
  const document = text(url.searchParams.get('document'), 256, 'document');
  const key = new Request(`${url.origin}/atlas/pairs?document=${encodeURIComponent(document ?? '')}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const rows = await env.DB.prepare(pairsQuery(Boolean(document))).bind(...(document ? [document] : [])).all<{ text: string; n: number }>();
  const body = { items: rows.results, limit: PAIRS_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
// One pair's occurrences: the two crops of each, in the order the pair's index keeps (by the first crop's
// id), each crop found by its key. A book's are read through the index it shares with the count. The
// join order is fixed and the origin test kept off its index (`+`): the planner would otherwise start
// from every local crop.
const PAIR_PAGE_MAX = 96, PAIR_OFFSET_MAX = 2000;
export function pairOccurrencesQuery(document: boolean) {
  return `SELECT a.data AS first, b.data AS second FROM unit_pairs p
    CROSS JOIN units a ON a.id=p.first AND +a.origin='local' CROSS JOIN units b ON b.id=p.second AND +b.origin='local'
    WHERE ${document ? 'p.document=? AND ' : ''}p.text=? ORDER BY p.first LIMIT ? OFFSET ?`;
}
export function pairCountQuery(document: boolean) {
  return `SELECT count(*) AS n FROM unit_pairs p
    CROSS JOIN units a ON a.id=p.first AND +a.origin='local' CROSS JOIN units b ON b.id=p.second AND +b.origin='local'
    WHERE ${document ? 'p.document=? AND ' : ''}p.text=?`;
}
async function pairOccurrences(env: Env, url: URL, pair: string) {
  const q = url.searchParams;
  const value = text(pair, 64, 'pair', true)!;
  const document = text(q.get('document'), 256, 'document');
  const limit = integer(q, 'limit', 48, PAIR_PAGE_MAX), offset = integer(q, 'offset', 0);
  if (offset > PAIR_OFFSET_MAX) throw new Problem(404, 'A pair does not page this far.');
  const bound = [...(document ? [document] : []), value];
  const [count, page] = await env.DB.batch([
    env.DB.prepare(pairCountQuery(Boolean(document))).bind(...bound),
    env.DB.prepare(pairOccurrencesQuery(Boolean(document))).bind(...bound, limit, offset),
  ]) as D1Result<any>[];
  const items = (page.results as { first: string; second: string }[])
    .map(row => ({ first: listing(parse(row.first)), second: listing(parse(row.second)) }));
  return { text: value, document, total: (count.results[0] as { n: number }).n, next_offset: offset + items.length, items };
}
async function catalogue(env: Env, ctx: ExecutionContext, url: URL) {
  const q = url.searchParams;
  const purpose = q.get('purpose') || 'browse';
  const production = q.get('production') || (purpose === 'review' ? REVIEW_SCOPE : 'all');
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const review = purpose === 'review';
  const seed = integer(q, 'seed', 0, 2147483647);
  const limit = integer(q, 'limit', 60, 96), offset = integer(q, 'offset', 0);
  if (review && offset > ROUND_OFFSET_MAX) throw new Problem(404, 'A round does not page this far.');
  const reviewer = q.get('reviewer') ? text(q.get('reviewer'), 128, 'reviewer', true)! : null;
  // One rest window for the counts and the listing.
  const since = restSince(), state = stateFor(reviewer, since);
  // An empty `reading` names no character.
  const reading = q.get('reading') || null;
  const scoped = review && reading !== null;
  const { where, values } = listingFilter(review, production, scoped ? reading : null);
  const [materials, materialValues] = material(production, 'production');
  const queries = facetsQueries(review, reviewer, where, scoped, since);
  const [stored, marked, skipped] = [queries.stored, queries.marked, queries.skipped].map(sql => sql ? env.DB.prepare(sql).bind(...values) : null);
  // Only counts that are the same for every visitor are cached; a reviewer's own skips are theirs.
  let groups: Facet[], published: D1Result<{ label: string; n: number }> | null = null;
  if (review) {
    const corpus = corpusCountQuery(production, scoped ? reading : null);
    const results = await env.DB.batch<any>([...[stored, marked, skipped].filter(s => s !== null), env.DB.prepare(corpus.sql).bind(...corpus.values)]);
    published = results.pop()!;
    groups = moved(results[0].results, results.slice(1).flatMap(result => result.results));
  } else {
    groups = await browseFacets(env, ctx, url, production, stored!, marked!);
    if (skipped) groups = moved(groups, (await skipped.all<Facet>()).results);
  }
  const categories = new Map<string, Json>(), documents = new Map<string, Json>();
  const counts: Json = { pending: 0, seen: 0, flagged: 0, checked: 0, hard: 0, skipped: 0 };
  const empty = { total: 0, pending: 0, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 };
  const add = (label: string, state: string, n: number, document: string | null = null, title: string | null = null, family: string | null = null) => {
    const category = categories.get(label) || { label, grapheme: graphemeOf(label, family), ...empty };
    category.total += n; category[state] += n; counts[state] += n;
    categories.set(label, category);
    if (!document) return;
    const book = documents.get(document) || { id: document, title, ...empty };
    book.total += n; book[state] += n;
    documents.set(document, book);
  };
  for (const row of groups) add(row.label, row.state, row.n, row.document, row.title, row.family);
  // A corpus glyph nothing has named is pending for everyone, and the table counts them per character.
  const corpus = new Map<string, number>();
  if (published) for (const row of published.results) if (row.n > 0) { corpus.set(row.label, row.n); add(row.label, 'pending', row.n) }
  // Browse's counts are grouped by character, book and state, so a browse listing filtered by those
  // alone takes its total from them; counting it again would read every crop it holds on each page.
  // Any other filter clears this, and the listing is counted.
  let tally: ((row: Facet) => boolean)[] | null = review ? null : [];
  if (reading && !scoped) { where.push('character=?'); values.push(reading); tally?.push(row => row.label === reading) }
  const document = text(q.get('document'), 256, 'document');
  if (document) { where.push('document=?'); values.push(document); tally?.push(row => row.document === document) }
  // A grapheme is a family's representative code point, or a label's own code points.
  const grapheme = text(q.get('grapheme'), 256, 'grapheme')?.toUpperCase().split(/\s+/).join(' ');
  if (grapheme) {
    if (!/^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*$/.test(grapheme) || grapheme.split(' ').some(p => parseInt(p.slice(2), 16) > 0x10FFFF))
      throw new Problem(422, 'Invalid grapheme.');
    // Every named crop has a family (its own code points when the character table gives none), so
    // this is one lookup that `unit_family_sample` serves in shuffle order.
    where.push('family=?'); values.push(grapheme); tally = null;
  }
  // A search finds a crop by its character or its reading. Each is one range of its own index; an OR
  // across the two columns would read every local crop instead. The origin test is kept off its index
  // (`+`), so the query starts from the ids the search found.
  if (q.get('q')) {
    where[0] = "+origin='local'";
    where.push("id IN (SELECT id FROM units WHERE origin='local' AND character=? UNION SELECT id FROM units WHERE origin='local' AND reading=?)");
    values.push(literal(q.get('q')!), literal(q.get('q')!)); tally = null;
  }
  // With a character, a grapheme or a search named, its own index finds the few crops and the script only filters
  // them (`+`); the script's index would read every crop of that script.
  if (q.get('group') && q.get('group') !== 'all') { where.push(reading || grapheme || q.get('q') ? '+category=?' : 'category=?'); values.push(q.get('group')!); tally = null }
  // `attention` is the Flagged view: every crop waiting for a person, flagged or hard to read. The
  // review state is worked out per crop, so the query starts from the few that can qualify.
  if (q.get('state') === 'attention') { where[0] = "+origin='local'"; where.push(ATTENTION_CANDIDATES, `${state} IN ('flagged','hard')`); tally = null }
  else if (q.get('state') && q.get('state') !== 'all') { const wanted = q.get('state')!; where.push(`${state}=?`); values.push(wanted); tally?.push(row => row.state === wanted) }
  // The Flagged view hides crops already looked at in the inspector by default; `reported=show`
  // (the default for every other caller) leaves them in. `reportedCountWhere` is captured before the
  // hide filter, so the count is of what is hidden, not what remains.
  const flaggedView = ['flagged', 'attention'].includes(q.get('state') ?? '');
  const reportedCountWhere = flaggedView ? [...where, REVIEWED_IN_INSPECTOR] : null;
  if (flaggedView && q.get('reported') === 'hide') { where.push(`NOT ${REVIEWED_IN_INSPECTOR}`); tally = null }
  // A round of one character deals its named and then its untouched corpus glyphs after its local crops.
  // Corpus glyphs belong to no work of the collection, so a round narrowed to one work deals none.
  const dealt = review && reading !== null && !document && ['all', 'pending'].includes(q.get('state') || 'all') && !q.get('q')
    && ['all', categoryOf(reading)].includes(q.get('group') || 'all');
  const named = dealt ? { sql: namedRoundQuery(materials, state), values: [reading, ...materialValues] } : null;
  const from = named ? `(SELECT * FROM units WHERE ${where.join(' AND ')} UNION ALL ${named.sql}) AS units` : `units WHERE ${where.join(' AND ')}`;
  const fromValues = named ? [...values, ...named.values] : values;
  // A crop another reviewer skipped comes first in a round: it needs a second pair of eyes. Then a
  // character's local crops, then its named corpus glyphs.
  const others = review ? `EXISTS(SELECT 1 ${SKIPS}${reviewer ? ` AND k.actor!=${quoted(reviewer)}` : ''}) DESC,origin='corpus',` : '';
  const order = others + (review && seed % 5 ? 'priority,' : '');
  // Flagged view: a crop already looked at in the inspector queues behind the ones nobody has reviewed yet.
  const reviewedLast = flaggedView ? `${REVIEWED_IN_INSPECTOR},` : '';
  const columns = `*,${state} AS effective,(SELECT shape_order FROM unit_shapes s WHERE s.id=units.id) AS shape_order,${SUSPECT} AS suspect`;
  // Browse deals crops in `shuffle` order from a point the seed picks, and wraps round past the
  // highest: an index serves that order, where a seed-scrambled order sorts every row on each visit.
  // A named character is found through `unit_character` instead, and its few crops sort in memory.
  const rotated = !review && !flaggedView && !reading && !q.get('q');
  const start = seed % SHUFFLE_RANGE;
  const side = (test: '>=' | '<') => `SELECT ${columns} FROM units WHERE ${where.join(' AND ')} AND shuffle${test}? ORDER BY shuffle,rowid LIMIT ? OFFSET ?`;
  const results = await env.DB.batch([
    ...(tally ? [] : [env.DB.prepare(`SELECT count(*) AS n FROM ${from}`).bind(...fromValues)]),
    rotated ? env.DB.prepare(side('>=')).bind(...values, start, limit, offset)
      : env.DB.prepare(`SELECT ${columns} FROM ${from} ORDER BY ${order}${reviewedLast} ((shuffle * ?) % 2147483647),id LIMIT ? OFFSET ?`).bind(...fromValues, seed + 1, limit, offset),
    ...(reportedCountWhere ? [env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${reportedCountWhere.join(' AND ')}`).bind(...values)] : []),
  ]);
  const count = tally ? null : results.shift()!, [window, reportedCount] = results;
  const listed = tally ? groups.filter(row => tally!.every(test => test(row))).reduce((n, row) => n + row.n, 0)
    : (count!.results[0] as { n: number }).n;
  const rows = window.results as (UnitRow & { effective: string; shape_order: number | null; suspect: string | null })[];
  if (rotated && rows.length < limit) {
    const above = rows.length ? offset + rows.length : (await env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${where.join(' AND ')} AND shuffle>=?`)
      .bind(...values, start).first<{ n: number }>())!.n;
    const wrapped = await env.DB.prepare(side('<')).bind(...values, start, limit - rows.length, Math.max(offset - above, 0)).all();
    rows.push(...wrapped.results as typeof rows);
  }
  const items: Json[] = rows
    .map(row => { const item = compact(row); return { ...item, state: row.effective, shape_order: row.shape_order,
      suspect: suspectOf(row.suspect ? parse(row.suspect) as Suspect : null, item) } });
  // Positions run through the `units` rows and then the untouched corpus glyphs. A glyph its record
  // keeps out still takes its position, so `next_offset` can run ahead of the items, and once the
  // glyphs run out `total` is what was there to deal.
  let total = listed, next = offset + items.length;
  const unnamed = dealt ? corpus.get(reading) || 0 : 0;
  if (dealt && unnamed) {
    total += unnamed;
    if (items.length < limit) {
      const round = await corpusRound(env, reading, production, seed, Math.max(offset - listed, 0), limit - items.length);
      // A glyph is one crop whichever list deals it, even should its published row read as untouched.
      const shown = new Set(items.map(item => item.id));
      items.push(...round.items.filter(item => !shown.has(item.id))); next += round.read;
      if (round.exhausted) total = next;
    }
  }
  return { total, next_offset: next, available: Object.values(counts).reduce((a:number,b:any) => a+b,0),
    counts, purpose, production, review_limit:ROUND_MAX, review_epoch: await meta(env, 'review_epoch') || 0, query: q.get('q'),
    categories: [...categories.values()].sort((a,b) => b.total-a.total || a.label.localeCompare(b.label)),
    documents: [...documents.values()].sort((a,b) => b.total-a.total || (a.title ?? '').localeCompare(b.title ?? '') || a.id.localeCompare(b.id)),
    reported_count: reportedCount ? (reportedCount.results[0] as { n: number }).n : 0,
    items };
}
// Untouched assigned corpus glyphs per character in this material.
export function corpusCountQuery(production: string, character: string | null = null) {
  const [materials, values] = material(production, 'production');
  return { sql: `SELECT character AS label,sum(n-named) AS n FROM corpus_characters WHERE ${character === null ? '' : 'character=? AND '}${materials} GROUP BY character`,
    values: character === null ? values : [character, ...values] };
}
// The round character's named corpus glyphs that are due, from its most recently named pending rows.
export function namedRoundQuery(materials: string, state: string) {
  return `SELECT * FROM (SELECT * FROM units WHERE origin='corpus' AND character=? AND state='pending' ORDER BY rowid DESC LIMIT ${NAMED_WINDOW}) AS units
    WHERE quiz=1 AND ${materials} AND ${state}='pending'`;
}
// One character's untouched corpus glyphs of one material, or of every material, in shuffle order
// from a point the seed picks.
export function corpusRoundQuery(production: string | null, side: '>=' | '<') {
  return `SELECT * FROM corpus_units WHERE character=?${production ? ' AND production=?' : ''} AND named=0 AND shuffle${side}?
    ORDER BY shuffle LIMIT ?`;
}
// The glyphs wrap round from the seeded point, so each seed deals a different but stable order that
// the index serves as it stands. A scope short of `all` reads each production the character holds in
// it through its own index range, and merges them.
async function corpusRound(env: Env, character: string, production: string, seed: number, offset: number, limit: number) {
  const start = seed % SHUFFLE_RANGE, wanted = offset + limit;
  const kinds = production === 'all' ? [null] : (await env.DB.prepare('SELECT production FROM corpus_characters WHERE character=?')
    .bind(character).all<{ production: string }>()).results.map(row => row.production).filter(value => inMaterial(production, value));
  if (!kinds.length) return { items: [], read: 0, exhausted: true };
  const page = async (side: '>=' | '<', n: number) => {
    const results = await env.DB.batch(kinds.map(kind => env.DB.prepare(corpusRoundQuery(kind, side))
      .bind(character, ...(kind ? [kind] : []), start, n)));
    return results.flatMap(r => r.results as CorpusRow[]).sort((a, b) => a.shuffle - b.shuffle || (a.id < b.id ? -1 : 1)).slice(0, n);
  };
  const rows = await page('>=', wanted);
  if (rows.length < wanted) rows.push(...await page('<', wanted - rows.length));
  const read = rows.slice(offset), items: Json[] = [];
  // Bound simultaneous R2 streams, as for a corpus search page.
  for (const batch of chunks(read, 8)) {
    for (const data of await Promise.all(batch.map(row => corpusData(env, row)))) {
      // The record decides: a glyph whose image this site may not serve, or whose record disagrees
      // with its published row about the character or the material, is not dealt.
      if (dealable('corpus', data) && data.label === character && inMaterial(production, productionOf(data)))
        items.push({ ...listing(data), origin: 'corpus', state: 'pending', shape_order: null, suspect: null });
    }
  }
  if (items.length) {
    const marks = await env.DB.prepare('SELECT id,p,reads_as,label,box FROM unit_suspects WHERE id IN (SELECT value FROM json_each(?))')
      .bind(JSON.stringify(items.map(item => item.id))).all<{ id: string; p: number; reads_as: string | null; label: string; box: string | null }>()
    const found = new Map(marks.results.map(row => [row.id, { ...row, box: row.box === null ? null : parse(row.box) }]))
    for (const item of items) item.suspect = suspectOf(found.get(item.id), item)
  }
  return { items, read: read.length, exhausted: rows.length < wanted };
}
// The homepage gallery is dealt from `corpus_gallery`: records copied out of the R2 packs for the corpus
// glyphs whose `shuffle` falls below SAMPLE_RANGE, so a page is one query where reading its records
// from the packs took one R2 request each. Packs are content-addressed, so a copy is current exactly
// while `corpus_units` still names the object and offset it came from; `scripts/fill_corpus_gallery.py`
// copies the rest after a publication. A glyph a review has named shows its `units` row.
const SAMPLE_RANGE = 4194304;
export const gallerySampleQuery = (side: '>=' | '<') => `SELECT s.data,u.data AS current,u.written_form AS current_form,${FORM_COLUMNS.split(',').map(c => `f.${c}`).join(',')}
  FROM corpus_gallery s JOIN corpus_units c ON c.id=s.id AND c.object=s.object AND c.offset=s.offset
  LEFT JOIN units u ON u.id=s.id LEFT JOIN form_units f ON f.id=s.id
  WHERE s.shuffle${side}? ORDER BY s.shuffle LIMIT ?`;
async function gallery(env: Env, q: URLSearchParams) {
  const limit = integer(q, 'limit', 24, 96), start = integer(q, 'seed', 0, 2147483647) % SAMPLE_RANGE;
  type Row = UnitForm & { data: string; current: string | null; current_form: string | null };
  const rows = (await env.DB.prepare(gallerySampleQuery('>=')).bind(start, limit).all<Row>()).results;
  if (rows.length < limit) rows.push(...(await env.DB.prepare(gallerySampleQuery('<')).bind(start, limit - rows.length).all<Row>()).results);
  const items = rows.map(r => r.current ? { ...parse(r.current), ...(r.current_form ? { written_form: r.current_form } : {}) }
    : formed(parse(r.data), r.id ? r : null, formTools));
  return { status: 'ok', available: items.length, items };
}
function chunks<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}
// A character's edges in the 異体字 graph, both ways (0033): each by the key or by `b`'s index.
export const variantEdgesQuery = () => `SELECT b AS other,relation,source,detail,widens FROM character_variants WHERE a=?
  UNION ALL SELECT a AS other,relation,source,detail,widens FROM character_variants WHERE b=? LIMIT 2000`;
// How many crops each of a bounded list of characters has here and in the corpus, from the counts the
// triggers keep (0032, 0006): one key range per character, never a scan of the crops.
export const variantCountsQuery = (n: number) => `SELECT character,sum(n) AS n FROM unit_counts WHERE origin='local' AND character IN (${Array(n).fill('?').join(',')}) GROUP BY character`;
export const variantCorpusCountsQuery = (n: number) => `SELECT character,sum(n) AS n FROM corpus_characters WHERE character IN (${Array(n).fill('?').join(',')}) GROUP BY character`;
// Each row of a card's variants lists at most this many, the most attested first; a gallery widens to
// exactly the first row. The Python layer's VARIANTS_SHOWN.
const VARIANTS_SHOWN = 32;
// A pair any source calls simplified is kept apart even where another lists it as a plain variant
// (refs.KEPT_APART): cjkvi pairs 干 with 乾 and 幹, which four sources give as simplifications.
const KEPT_APART = new Set(['simplified']);
// The characters a gallery widened to its variants deals with `char`: the card's first row, in the
// card's order (most sources, then code point), at most VARIANTS_SHOWN. One grouped read of the
// character's edges, both ways by key and index.
export const writtenVariantsQuery = () => `SELECT other FROM (
    SELECT b AS other,relation,source,widens FROM character_variants WHERE a=?
    UNION ALL SELECT a AS other,relation,source,widens FROM character_variants WHERE b=?)
  WHERE other<>? GROUP BY other HAVING max(widens)=1 AND max(relation IN ('simplified'))=0
  ORDER BY count(DISTINCT source) DESC,other LIMIT ${VARIANTS_SHOWN}`;
async function writtenVariants(env: Env, char: string): Promise<string[]> {
  const rows = (await env.DB.prepare(writtenVariantsQuery()).bind(char, char, char).all<{ other: string }>()).results;
  return [char, ...rows.map(row => row.other)];
}
// A gallery widened to its variants pages this far at most and counts no further: the variants of a
// common character can hold a hundred thousand corpus glyphs.
const WIDENED_CAP = 2000;
const widenedPage = (offset: number) => { if (offset > WIDENED_CAP) throw new Problem(404, 'A widened gallery does not page this far.') };
// A character's crops with its variants', in the order `unit_character_style` holds them: by character,
// then style (`STYLE_ORDER`), then id.
export const widenedCropsQuery = (n: number, extra = '') => `SELECT * FROM units WHERE origin=? AND character IN (${Array(n).fill('?').join(',')})${extra} ORDER BY character,style_order,id LIMIT ? OFFSET ?`;
export const widenedCropsCountQuery = (n: number, extra = '') => `SELECT count(*) AS n FROM (SELECT 1 FROM units WHERE origin=? AND character IN (${Array(n).fill('?').join(',')})${extra} LIMIT ${WIDENED_CAP + 1})`;
// A corpus category: its glyphs, those corrected into it and not out of it, each branch in its own
// index order (`k`, `s`, `i`: `corpus_character_style` or `corpus_family_style`, and
// `unit_corpus_character_style`) so the union merges without sorting. `field` is character or family.
// A glyph's style is its published row's; a named glyph's `units` row carries the same.
export const corpusSelection = (field: 'character' | 'family', n: number) => {
  const list = n === 1 ? '=?' : ` IN (${Array(n).fill('?').join(',')})`;
  return `SELECT c.*,c.${field} AS k,c.style_order AS s,c.id AS i,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character,u.written_form AS overlay_form
    FROM corpus_units c LEFT JOIN units u ON c.id=u.id
    WHERE c.${field}${list} AND (u.id IS NULL OR u.${field}=c.${field})
    UNION ALL SELECT c.*,u.${field} AS k,u.style_order AS s,u.id AS i,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character,u.written_form AS overlay_form
    FROM units u${field === 'character' ? ' INDEXED BY unit_corpus_character_style' : ''} JOIN corpus_units c ON c.id=u.id
    WHERE u.origin='corpus' AND u.${field}${list} AND c.${field} IS NOT u.${field}`;
};
// The style groups a gallery is filtered by, as `style_order` (migration 0035) numbers them: running
// and cursive script, then what nobody has judged, then the formal scripts and the print faces. A gallery lists them in
// this order.
export const STYLE_ORDER: Record<string, number> = { cursive: 0, unassessed: 1, formal: 2 };
const STYLE_NAMES = Object.keys(STYLE_ORDER);
function styleGroup(q: URLSearchParams): number | null {
  const value = q.get('style');
  if (!value || value === 'all') return null;
  if (!Object.hasOwn(STYLE_ORDER, value)) throw new Problem(422, 'Invalid style.');
  return STYLE_ORDER[value];
}
// A gallery names the style groups it can be filtered by (`style_groups`); a server that reports none
// cannot filter by style.
// Counts by `style_order` as a gallery reports them: every group by name, and the total of the one asked
// for, or of all.
function styleCounts(rows: { s: number; n: number }[], group: number | null) {
  const styles = Object.fromEntries(STYLE_NAMES.map(name => [name, rows.find(row => row.s === STYLE_ORDER[name])?.n ?? 0]));
  const total = group === null ? rows.reduce((sum, row) => sum + row.n, 0) : rows.find(row => row.s === group)?.n ?? 0;
  return { styles, total };
}
type VariantEdge = { other: string; relation: string; source: string; detail: string; widens: number };
type VariantRow = { char: string; code_point: string; widens: boolean; relations: { relation: string; source: string; detail: string }[] };
// The characters `char` shares an edge with, as the card lists them: `items` a gallery widens to (a
// pair with a widening edge and none that keeps it apart), `related` the rest; each most attested
// first (sources, then code point), every edge kept. `sources` cites each source used.
async function variantsOf(env: Env, ctx: ExecutionContext, origin: string, char: string) {
  const key = new Request(`${origin}/layers/variants?c=${encodeURIComponent(char)}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json();
  const edges = (await env.DB.prepare(variantEdgesQuery()).bind(char, char).all<VariantEdge>()).results;
  const byChar = new Map<string, VariantRow & { edges: VariantEdge[] }>();
  for (const edge of edges) {
    if (edge.other === char) continue;
    const entry = byChar.get(edge.other) ?? { char: edge.other, code_point: cp(edge.other), widens: false, relations: [], edges: [] };
    entry.edges.push(edge);
    entry.relations.push({ relation: edge.relation, source: edge.source, detail: edge.detail });
    byChar.set(edge.other, entry);
  }
  const attested = (entry: VariantRow) => new Set(entry.relations.map(r => r.source)).size;
  const ordered = [...byChar.values()].map(({ edges: pair, ...entry }) => ({ ...entry,
    widens: pair.some(e => e.widens) && !pair.some(e => KEPT_APART.has(e.relation)) }))
    .sort((a, b) => attested(b) - attested(a) || (a.char.codePointAt(0)! - b.char.codePointAt(0)!));
  const shown = [...ordered.filter(e => e.widens).slice(0, VARIANTS_SHOWN), ...ordered.filter(e => !e.widens).slice(0, VARIANTS_SHOWN)];
  const chars = shown.map(entry => entry.char);
  const [local, corpus] = chars.length ? await env.DB.batch([
    env.DB.prepare(variantCountsQuery(chars.length)).bind(...chars),
    env.DB.prepare(variantCorpusCountsQuery(chars.length)).bind(...chars),
  ]) as D1Result<{ character: string; n: number }>[] : [null, null];
  const count = (rows: { character: string; n: number }[] | undefined) => new Map((rows ?? []).map(row => [row.character, row.n]));
  const here = count(local?.results), there = count(corpus?.results);
  const cited: Record<string, string> = (await meta(env, 'variant_sources')) || {};
  const rows = shown.map(entry => ({ ...entry, sources: [...new Set(entry.relations.map(r => r.source))].sort(),
    count: here.get(entry.char) ?? 0, corpus_count: there.get(entry.char) ?? 0 }));
  const used = new Set(rows.flatMap(row => row.sources));
  const found = { items: rows.filter(row => row.widens), related: rows.filter(row => !row.widens), total: byChar.size,
    sources: Object.fromEntries([...used].sort().map(source => [source, cited[source] ?? source])) };
  ctx.waitUntil(caches.default.put(key, Response.json(found, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return found;
}
async function known(env: Env, value: string) {
  const key = cp(literal(value));
  const row = await env.DB.prepare('SELECT data,detail FROM characters WHERE code_point=?').bind(key).first<{data:string;detail:string}>();
  if (!row) throw new Problem(404, 'Character not found.');
  return { data: parse(row.data), detail: parse(row.detail) };
}
// What a corrected character reads as, by the review server's rule: a kana's one stated reading, a
// ligature's reading, a kanji itself; several stated readings are a person's choice, so none.
export function readingFrom(data: Json | null | undefined): string | null {
  if (!data) return null;
  if (data.ligature?.reading) return hira(data.ligature.reading);
  const readings: string[] = data.readings || [];
  if (readings.length === 1) return readings[0];
  return !readings.length && data.script === 'han' ? data.char : null;
}
async function suggest(env: Env, q: URLSearchParams) {
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
  return { query: term, items: rows.results.slice(0,limit).map(r => ({...parse(r.data), rank:r.rank})),
    total: rows.results.length, more: Math.max(0,rows.results.length-limit), status:'ok',corpus:{ready:true} };
}
async function occurrences(env: Env, code: string, q: URLSearchParams, origin = 'local') {
  const { data } = await known(env, code);
  if(origin==='corpus')return corpusOccurrences(env,data,q);
  const limit = integer(q,'limit',24,200), offset=integer(q,'offset',0), group = styleGroup(q);
  // The filters other than style: the style counts are taken over them, so each group says what it holds.
  const filters: string[] = [], extra: (string | number)[] = [];
  if (q.get('visual_group')) {
    if (q.get('visual_group') === 'unassigned') filters.push('visual_group IS NULL');
    else { filters.push('visual_group=?'); extra.push(q.get('visual_group')!) }
  }
  if (q.get('state') && q.get('state') !== 'all') { filters.push('state=?'); extra.push(q.get('state')!) }
  const tail = filters.map(f => ' AND ' + f).join('');
  const styled = group === null ? tail : tail + ' AND style_order=?', styledExtra = group === null ? extra : [...extra, group];
  if (q.get('scope') === 'variants' || q.get('expand') === 'variants') {
    widenedPage(offset);
    const chars = await writtenVariants(env, data.char);
    const [count, rows] = await env.DB.batch([
      env.DB.prepare(widenedCropsCountQuery(chars.length, styled)).bind(origin, ...chars, ...styledExtra),
      env.DB.prepare(widenedCropsQuery(chars.length, styled)).bind(origin, ...chars, ...styledExtra, limit, offset),
    ]);
    const counted = (count.results[0] as { n: number }).n, total = Math.min(counted, WIDENED_CAP);
    return { ...data.candidates, query: data.code_point, code_point: data.code_point,
      total, capped: counted > WIDENED_CAP, available: rows.results.length, items: (rows.results as UnitRow[]).map(compact),
      counts: { total, exact: total, exact_total: total }, style_groups: STYLE_NAMES, scope: 'variants', status: 'ok' };
  }
  let counted: D1PreparedStatement, listed: D1PreparedStatement;
  if (q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme') {
    // A grapheme's crops are its family's and its own character's. Each is one range of its own index
    // (`unit_family_style`, `unit_character_style`), and the page merges the two in style and id order;
    // an OR across the two columns would read every crop of the origin instead.
    const family = data.grapheme?.code_point || data.code_point;
    counted = env.DB.prepare(graphemeCountsQuery(tail)).bind(origin, family, origin, data.char, ...extra);
    listed = env.DB.prepare(graphemeCropsQuery(styled)).bind(origin, family, ...styledExtra, origin, data.char, family, ...styledExtra, limit, offset);
  } else {
    counted = env.DB.prepare(`SELECT style_order AS s,count(*) AS n FROM units WHERE origin=? AND character=?${tail} GROUP BY 1`)
      .bind(origin, data.char, ...extra);
    listed = env.DB.prepare(characterCropsQuery(styled)).bind(origin, data.char, ...styledExtra, limit, offset);
  }
  const [count, rows] = await env.DB.batch([counted, listed]);
  const { styles, total } = styleCounts(count.results as { s: number; n: number }[], group);
  return { ...data.candidates, query: data.code_point, code_point: data.code_point,
    total, available: rows.results.length, items:(rows.results as UnitRow[]).map(compact),
    counts:{ total, exact:total, exact_total:total }, styles, style_groups: STYLE_NAMES, scope:q.get('scope') || 'character', status:'ok' };
}
// A grapheme's crops counted by style group, each branch read along its own index.
export const graphemeCountsQuery = (extra = '') => `SELECT style_order AS s,count(*) AS n FROM units
  WHERE id IN (SELECT id FROM units WHERE origin=? AND family=? UNION SELECT id FROM units WHERE origin=? AND character=?)${extra} GROUP BY 1`;
// A character's crops, and a grapheme's, in style order (`STYLE_ORDER`) then id; `extra` is further
// conditions on the crop.
export const characterCropsQuery = (extra = '') => `SELECT * FROM units WHERE origin=? AND character=?${extra} ORDER BY style_order,id LIMIT ? OFFSET ?`;
// A grapheme's crops are its family's, and those of its own character filed under another family; the
// two branches share no crop, so they merge without comparing whole rows.
export const graphemeCropsQuery = (extra = '') => `SELECT * FROM units WHERE origin=? AND family=?${extra}
  UNION ALL SELECT * FROM units WHERE origin=? AND character=? AND family IS NOT ?${extra} ORDER BY style_order,id LIMIT ? OFFSET ?`;
async function corpusOccurrences(env:Env,data:Json,q:URLSearchParams){
  const limit=integer(q,'limit',24,200),offset=integer(q,'offset',0),group=styleGroup(q);
  const family=q.get('scope')==='grapheme',widened=q.get('scope')==='variants',field=family?'family':'character';
  if(widened)widenedPage(offset);
  // A variants widening reads the character and its variants, each by the character index.
  const selected=widened?await writtenVariants(env,data.char):[family?(data.grapheme?.code_point||data.code_point):data.char];
  // Unassigned source classes remain null until evidence identifies the form.
  const where=['1=1'],values:(string|number)[]=[...selected,...selected];
  if(q.get('visual_group')){if(q.get('visual_group')==='unassigned')where.push('(CASE WHEN overlay IS NULL THEN character ELSE overlay_character END) IS NULL');
    else{where.push('(CASE WHEN overlay IS NULL THEN visual_group ELSE overlay_group END)=?');values.push(q.get('visual_group')!)}}
  const join=`FROM (${corpusSelection(field,selected.length)})`;
  const styled=group===null?where:[...where,'s=?'],styledValues=group===null?values:[...values,group];
  const [count,rows]=await env.DB.batch([
    // A widening counts no further than its cap, and so has no style counts.
    widened?env.DB.prepare(`SELECT count(*) AS n FROM (SELECT 1 ${join} WHERE ${styled.join(' AND ')} LIMIT ${WIDENED_CAP+1})`).bind(...styledValues)
      :env.DB.prepare(`SELECT s,count(*) AS n ${join} WHERE ${where.join(' AND ')} GROUP BY s`).bind(...values),
    env.DB.prepare(`SELECT * ${join} WHERE ${styled.join(' AND ')} ORDER BY k,s,i LIMIT ? OFFSET ?`).bind(...styledValues,limit,offset),
  ]);
  const grouped=widened?null:styleCounts(count.results as {s:number;n:number}[],group);
  const counted=grouped?grouped.total:(count.results[0] as {n:number}).n;
  const items=[];
  // Bound simultaneous R2 streams; a corpus page may contain 200 records.
  for(let i=0;i<rows.results.length;i+=8){
    items.push(...await Promise.all((rows.results.slice(i,i+8) as (CorpusRow&{overlay:string|null;overlay_form:string|null})[])
      .map(async row=>({...(row.overlay?parse(row.overlay):await corpusData(env,row)),style:row.style,...(row.overlay_form?{written_form:row.overlay_form}:{})}))));
  }
  const info=await known(env,data.char),familyCode=data.grapheme?.code_point||data.code_point;
  const familyCounts=await env.DB.prepare(`SELECT count(*) AS total,sum(written IS NULL) AS unassigned FROM (
    SELECT CASE WHEN u.id IS NULL THEN c.character ELSE u.character END AS written
      FROM corpus_units c LEFT JOIN units u ON c.id=u.id WHERE c.family=? AND (u.id IS NULL OR u.family=c.family)
    UNION ALL SELECT u.character AS written FROM units u JOIN corpus_units c ON c.id=u.id
      WHERE u.origin='corpus' AND u.family=? AND c.family IS NOT u.family
    )`).bind(familyCode,familyCode).first<{total:number;unassigned:number}>();
  return {...data.candidates,code_point:data.code_point,total:widened?Math.min(counted,WIDENED_CAP):counted,capped:widened&&counted>WIDENED_CAP,
    available:items.length,items,...(grouped?{styles:grouped.styles}:{}),scope:family?'grapheme':widened?'variants':'character',status:'ok',
    visual_analysis:info.detail.visual_analysis,family_total:familyCounts?.total||0,unassigned_count:familyCounts?.unassigned||0};
}
// A document's characters in source order. The primary key serves the filter and the order, and each
// row joins its unit by id: a published unit gives its current reading, state and revision.
export const documentCharactersQuery = () => `SELECT c.unit,c.data,u.character,u.state,u.revision,json_extract(u.data,'$.issue') AS issue
  FROM document_characters c LEFT JOIN units u ON u.id=c.unit WHERE c.document=? ORDER BY c.ord`;
// Other sites read this listing from the browser, so it is served to any origin, errors included. The
// edge copy is keyed by the catalogue version, so any change to the units makes a new key; browsers
// revalidate every time.
const OPEN = { 'access-control-allow-origin': '*' };
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
async function media(env: Env, request: Request, key: string, ctx: ExecutionContext) {
  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;
  if (!/^[a-f0-9]{64}$/.test(key)) throw new Problem(404,'Image not found.');
  const row = await env.DB.prepare('SELECT * FROM media WHERE key=?').bind(key)
    .first<{ object:string;offset:number;size:number;content_type:string }>();
  if(!row) throw new Problem(404,'Image not found.');
  if(request.headers.get('if-none-match')===`"${key}"`) return new Response(null,{status:304});
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object) throw new Problem(503,'Image publication is incomplete.');
  const response=new Response(object.body,{headers:{'content-type':row.content_type,'content-length':String(row.size),
    'cache-control':'public, max-age=31536000, immutable','etag':`"${key}"`,'x-content-type-options':'nosniff'}});
  ctx.waitUntil(cache.put(request,response.clone()));
  return response;
}
async function body(request: Request): Promise<Json> {
  const origin=request.headers.get('origin');
  if(origin && origin!==new URL(request.url).origin) throw new Problem(403,'Use the review form on this site.');
  if(!request.headers.get('content-type')?.startsWith('application/json')) throw new Problem(415,'Expected JSON.');
  const reader=request.body?.getReader();
  if(!reader) throw new Problem(422,'Empty request.');
  let size=0; const chunks:Uint8Array[]=[];
  while(true) {const {done,value}=await reader.read();if(done)break;size+=value.length;
    if(size>128*1024){await reader.cancel();throw new Problem(413,'Review is too large.')}chunks.push(value)}
  const bytes=new Uint8Array(size);let pos=0;for(const part of chunks){bytes.set(part,pos);pos+=part.length}
  try{return JSON.parse(new TextDecoder().decode(bytes))}catch{throw new Problem(422,'Invalid JSON.')}
}
function text(value: unknown, max: number, name: string, required=false): string | null {
  if(value==null && !required) return null;
  if(typeof value!=='string'||value.length>max||(required&&!value.trim()))throw new Problem(422,`Invalid ${name}.`);
  return compose(value.trim());
}
// A character's grapheme family, as a reviewed correction takes it; one the catalogue lacks is its own.
const formTools: FormTools = {fail:(status,message)=>{throw new Problem(status,message)},body,text,codePoints:cp,
  family:async(env,char)=>(await known(env,char).catch(()=>null))?.data.grapheme?.code_point||cp(char)};
export function canonical(value: unknown): string {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical((value as Json)[k])).join(',')+'}';
}
function validateAnswer(answer: Json, current: Json, round: boolean, corpus=false) {
  if(!Number.isSafeInteger(answer.revision)||answer.revision!==current.revision)throw new Problem(409,'This character changed. Reload it.');
  if(corpus?answer.source_revision!==current.source_revision:answer.image_sha256!==current.image_sha256)throw new Problem(409,'The source image changed. Reload it.');
  if(!['match','wrong','unsure'].includes(answer.verdict))throw new Problem(422,'Choose a review decision.');
  if(answer.issue!=null&&!['character','reading','merged','crop','blank','other','unclear'].includes(answer.issue))throw new Problem(422,'Unknown issue.');
  if(answer.verdict==='match'&&(answer.character||answer.correction||(round&&answer.issue)||(!round&&answer.issue&&answer.issue!=='reading')))
    throw new Problem(422,'A matching crop cannot also have an issue.');
  if(answer.verdict==='wrong'&&!answer.issue)throw new Problem(422,'Choose an issue.');
  for(const field of ['character','correction','reading'])text(answer[field],32,field);
  text(answer.note,2000,'note');
  if(answer.character)writtenCharacter(answer.character);
  if(answer.verdict==='wrong' && answer.character && literal(answer.character)===current.written_character)
    throw new Problem(422,'Choose a different character or a different issue.');
  if(answer.box) throw new Problem(422,'Crop geometry changes are queued as crop issues on this publication.');
}
async function submit(env: Env, request: Request, target?: string) {
  const input=await body(request);
  const corpus=target==='@corpus', batch=target==='@batch';
  if(corpus)target=text(input.identity,512,'corpus identity',true)!;
  if(batch)target=undefined;
  const id=text(input.id,64,'submission id',true)!, actor=text(input.client_id,128,'reviewer',true)!;
  if(!/^[0-9a-f-]{36}$/i.test(id))throw new Problem(422,'Invalid submission id.');
  // A retry is the same submission whatever else came on screen meanwhile: the seen crops are left
  // out of the signature, as the local server compares only the answers, and the first result stands.
  const {seen:_,skipped:__,...signed}=input;
  const signature=canonical({target:batch?'@batch':target||null,input:signed});
  const key=actor+':'+id;
  const previous=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
  if(previous){if(previous.request!==signature)throw new Problem(409,'This submission was already saved with different answers.');return parse(previous.response)}
  const round=!target&&!batch;
  if(round)text(input.label,32,'label',true);
  if(batch&&(input.seen!==undefined||input.skipped!==undefined))throw new Problem(422,'Only a round records seen or skipped crops.');
  const correction=batch?validBatch(input):null;
  const {answers,seen,skipped}=batch?{answers:correction!.crops,seen:[],skipped:[]}:validRound(input,target);
  const rows=await unitsById(env,answers.map(answer=>text(answer.id,512,'character id',true)!));
  // A correction names crops a reader selected on the grid, so each is judged here: one that changed
  // since the page loaded, one whose image cannot be shown, or one a person already checked as another
  // character is refused (all are named, and nothing is saved); one already written as the character
  // is confirmed, unless a person already checked it, when it is left as it is.
  const unchanged:string[]=[];
  if(batch){
    const refused:Json[]=[], chosen:Json[]=[];
    for(const crop of answers){
      const row=rows.get(crop.id)!;
      if(row instanceof Problem){refused.push({id:crop.id,reason:'missing'});continue}
      const data=parse(row.data), glyph=row.origin==='corpus';
      if(!Number.isSafeInteger(crop.revision)||crop.revision!==data.revision||(glyph?crop.source_revision!==data.source_revision:crop.image_sha256!==data.image_sha256)){refused.push({id:crop.id,reason:'changed'});continue}
      if(glyph&&!data.proxyable){refused.push({id:crop.id,reason:'unavailable'});continue}
      const identity=glyph?data.written_character:(data.written_character||data.label);
      if(identity===correction!.character){
        if(data.state==='checked')unchanged.push(crop.id);
        else chosen.push({...crop,verdict:'match'});
      }else if(data.state==='checked')refused.push({id:crop.id,reason:'checked'});
      else chosen.push({...crop,verdict:'wrong',issue:'character',character:correction!.character});
    }
    if(refused.length)throw new Problem(409,'Some of these crops changed or were already checked. Reload them.',{targets:refused});
    answers.splice(0,answers.length,...chosen);
  }
  // One round usually corrects many crops to the same few characters; each is read once.
  const lookups=new Map<string,Promise<{data:Json;detail:Json}|null>>();
  const lookup=(value:string)=>{let key:string;try{key=cp(literal(value))}catch{key='\u0000'+value}if(!lookups.has(key))lookups.set(key,known(env,value).catch(()=>null));return lookups.get(key)!};
  const changes=[];
  // Corpus glyphs this submission names for the first time; each gets its `units` row first.
  const fresh:(UnitRow&{fresh:CorpusRow})[]=[];
  const at=new Date().toISOString();
  for(const answer of answers){
    const found=rows.get(answer.id)!;
    if(found instanceof Problem)throw found;
    const row=found, stored=parse(row.data), glyph=row.origin==='corpus';
    const current:Json={...stored,category:row.category||categoryOf(stored.label)};
    row.data=JSON.stringify(current);
    validateAnswer(answer,current,round,glyph);
    if(answer.reading&&!single(answer.reading)){
      const identity=answer.character||current.written_character||current.label;
      const registered=await lookup(identity);
      if(!registered?.data.ligature?.reading||hira(registered.data.ligature.reading)!==hira(answer.reading))
        throw new Problem(422,'Use the registered ligature reading or one character.');
    }
    if(glyph&&(!current.proxyable||(current.identity_status==='unassigned'&&answer.verdict==='match')))
      throw new Problem(422,'Choose a written character or report an issue.');
    if(round&&(!row.quiz||current.label!==input.label))throw new Problem(409,'This round changed. Reload it.');
    const written=answer.character?literal(answer.character):null;
    // A corrected character carries its reading along unless one was typed: い corrected to り reads り.
    const derived=written&&!answer.reading?readingFrom((await lookup(written))?.data):null;
    const reading=answer.reading || (answer.issue==='reading'&&answer.correction&&single(answer.correction)?answer.correction:null)
      || (derived&&derived!==current.reading?derived:null);
    // A batch names the character only: a crop reported for its box, a blank or a merge stays reported.
    const kept=batch&&current.state==='flagged'&&current.issue&&!['character','reading'].includes(current.issue)?current.issue:null;
    const resolved=!kept&&(answer.verdict==='match'||Boolean(answer.issue==='character'&&written)||Boolean(answer.issue==='reading'&&reading));
    const family=written?(await lookup(written))?.data.grapheme?.code_point:null;
    const next:Json={...current,revision:current.revision+1,state:resolved?'checked':'flagged',
      ...(written?{label:written,char:written,code_point:cp(written),written_character:written,identity_status:'assigned',identity_basis:'human_review',script:/\p{Script=Katakana}/u.test(written)?'katakana':/\p{Script=Hiragana}/u.test(written)?'hiragana':/\p{Script=Han}/u.test(written)?'han':/\p{Script=Hangul}/u.test(written)?'hangul':isGugyeol(written)?'gugyeol':'symbol'}:{}),
      ...(written?{grapheme:family||cp(written),visual_group:null,category:categoryOf(written)}:{}),
      ...(reading?{reading}:{}),issue:resolved?null:kept??answer.issue};
    const snapshot={...parse(row.snapshot),character:compact(row)};
    // A round or batch keeps its request once, on the submission; each event names it and carries only its
    // own answer, so a submission of many crops stays well inside D1's row size. A single crop's review
    // keeps its request whole: it is that one answer.
    const evidence={kind:round?'visual-quiz':'character-review',...(round?{round:id,label:input.label}:{}),...(batch?{batch:id}:round?{answer}:{request:input}),
      verdict:answer.verdict,issue:answer.issue||null,note:answer.note||'',
      suggested_character:written?cp(written):null,suggested_reading:answer.correction||null,snapshot,
      correction:{unicode:cp(next.label),reading:next.reading,box:next.box}};
    const event={id:'cf:'+crypto.randomUUID(),target_type:'unit',target_id:row.id,field:'review',
      old:current.state==='checked'?'reviewed':current.state==='flagged'?'disputed':'machine',
      new:resolved?'reviewed':'disputed',role:'reviewer',actor,evidence:JSON.stringify(evidence),at};
    changes.push({row,next,event,snapshot});
    if(row.fresh)fresh.push(row as UnitRow&{fresh:CorpusRow});
  }
  // A crop that left the queue, changed its pixels or moved to another character since the round
  // was dealt was not seen as it stands, so it is skipped rather than failing the round.
  const shown:Json[]=[];
  const passed:Json[]=[];
  const named=[...seen.map(crop=>[crop,shown]),...skipped.map(crop=>[crop,passed])] as [Json,Json[]][];
  for(const batch of chunks(named,8)){
    const rows=await Promise.all(batch.map(([crop])=>unit(env,crop.id).catch(error=>{if(error instanceof Problem&&error.status===404)return null;throw error})));
    batch.forEach(([crop,list],i)=>{
      const row=rows[i];
      if(!row||!row.quiz)return;
      const data=parse(row.data);
      // `image_sha256` names the page here, so a crop re-cut on the same page would still match it;
      // the crop's own image is what the reader saw. A client that does not send it is held to the rest.
      // A corpus glyph's source revision covers its box and image reference.
      const same=row.origin==='corpus'?data.source_revision===crop.source_revision:data.image_sha256===crop.image_sha256;
      if(same&&data.label===input.label&&(crop.image===undefined||crop.image===data.image)){
        list.push(crop);
        if(row.fresh)fresh.push(row as UnitRow&{fresh:CorpusRow});
      }
    });
  }
  const result=corpus?{...(changes[0].next),origin:'corpus',written_form:changes[0].row.written_form??null,event:changes[0].event}
    :batch?{id,results:changes.map(c=>({target_id:c.row.id,revision:c.next.revision,state:c.next.state})),unchanged}
    :{id,results:[...changes.map(c=>({id:c.event.id,target_id:c.row.id,field:'review',revision:c.next.revision,state:c.next.state})),
      ...shown.map(crop=>({target_id:crop.id,field:'seen'})),...passed.map(crop=>({target_id:crop.id,field:'skip'}))]};
  const statements=[env.DB.prepare('INSERT INTO submissions(id,actor,request,response,at) VALUES (?,?,?,?,?)').bind(key,actor,signature,JSON.stringify(result),at)];
  for(const row of fresh)statements.push(materialise(env,row));
  for(const c of changes){
    statements.push(env.DB.prepare(`INSERT INTO events(id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`)
      .bind(c.event.id,key,c.row.id,actor,c.row.revision,c.row.data,JSON.stringify(c.next),JSON.stringify(c.event),c.row.origin==='corpus'?c.row.snapshot:JSON.stringify(c.snapshot),'review',at));
  }
  // The box is copied from the row itself, so the queue compares it with the same JSON text. A corpus
  // glyph is recorded with its source revision in place of the page hash.
  for(const crop of shown)statements.push(env.DB.prepare(
    "INSERT INTO seen(target,submission,box,image_sha256,at) SELECT id,?,json_extract(data,'$.box'),?,? FROM units WHERE id=?")
    .bind(key,pixels(crop),at,crop.id));
  for(const crop of passed)statements.push(env.DB.prepare(
    "INSERT INTO skips(target,submission,actor,box,image_sha256,at) SELECT id,?,?,json_extract(data,'$.box'),?,? FROM units WHERE id=?")
    .bind(key,actor,pixels(crop),at,crop.id));
  try{await env.DB.batch(statements)}catch(error){
    const repeat=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
    if(repeat?.request===signature)return parse(repeat.response);
    if(String(error).includes('review_revision_conflict'))throw new Problem(409,'Another review changed this crop. Reload it.');
    throw error;
  }
  return result;
}
const FORM_PROBLEMS: Record<FormProblem, string> = {
  character: 'Write one character or an ideographic description sequence.',
  component: 'A description is built from ideographs, radicals and strokes.',
  missing: 'This description is missing a component.',
  extra: 'This description has more components than its operators take.',
};
// A reviewer's word on what a crop's letterforms are written as (0038), for a crop by its path or a
// corpus glyph by `identity`. It names the revision and pixels the reviewer saw, and a crop that has
// moved on since is refused; the save moves nothing else, so a review open against the crop still
// saves. A form that is the crop's own character clears it. A retry answers with the crop as it is,
// and the same id sent with anything else is refused.
async function writeForm(env: Env, request: Request, target: string | null) {
  const input = await body(request);
  const id = text(input.id, 64, 'submission id', true)!, actor = text(input.client_id, 128, 'reviewer', true)!;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Problem(422, 'Invalid submission id.');
  const crop = target ?? text(input.identity, 512, 'corpus identity', true)!;
  const key = actor + ':' + id, signature = canonical({ target: crop, input });
  const repeat = async () => {
    const saved = await env.DB.prepare('SELECT request FROM written_forms WHERE submission=?').bind(key).first<{ request: string }>();
    if (saved && saved.request !== signature) throw new Problem(409, 'This written form was already saved with different values.');
    return saved ? record(await unit(env, crop)) : null;
  };
  const previous = await repeat();
  if (previous) return previous;
  const row = await unit(env, crop), data = parse(row.data), glyph = row.origin === 'corpus';
  if (!Number.isSafeInteger(input.revision) || input.revision !== row.revision) throw new Problem(409, 'This character changed. Reload it.');
  const seen = glyph ? input.source_revision : input.image_sha256;
  if (typeof seen !== 'string' || seen !== (glyph ? data.source_revision : data.image_sha256)) throw new Problem(409, 'The source image changed. Reload it.');
  const typed = input.form == null ? null : text(input.form, 256, 'written form');
  let form = typed ? literal(typed) : null;
  if (form === data.label) form = null;
  const problem = form === null ? null : formProblem(form);
  if (problem) throw new Problem(422, FORM_PROBLEMS[problem]);
  const statements = row.fresh ? [materialise(env, row as UnitRow & { fresh: CorpusRow })] : [];
  statements.push(env.DB.prepare('INSERT INTO written_forms(id,submission,target,actor,revision,pixels,label,form,request,at) VALUES(?,?,?,?,?,?,?,?,?,?)')
    .bind('cf:' + crypto.randomUUID(), key, row.id, actor, row.revision, seen, data.label, form, signature, new Date().toISOString()));
  try { await env.DB.batch(statements) } catch (error) {
    const again = await repeat();
    if (again) return again;
    if (String(error).includes('written_form_revision_conflict')) throw new Problem(409, 'Another review changed this crop. Reload it.');
    throw error;
  }
  return { ...data, origin: row.origin, written_form: form };
}
// Every written form saved here, oldest first, for `glyph_atlas.review.cloudflare_import`: the crop,
// the label, revision and pixels the reviewer saw, and whether it is the crop's latest.
export const writtenFormsQuery = () => `SELECT w.id,w.target,u.origin,w.actor,w.revision,w.pixels,w.label,w.form,w.at,
  w.rowid=(SELECT max(l.rowid) FROM written_forms l WHERE l.target=w.target) AS current
  FROM written_forms w JOIN units u ON u.id=w.target ORDER BY w.rowid`;
async function writtenForms(env: Env) {
  const rows = await env.DB.prepare(writtenFormsQuery()).all<Json>();
  return { version: 1, kind: 'atlas-written-forms', publication: await meta(env, 'published_at'),
    forms: rows.results.map(row => ({ ...row, current: Boolean(row.current) })) };
}
async function undo(env:Env,request:Request,id:string){
  const input=await body(request),actor=text(input.client_id,128,'reviewer',true)!,key=actor+':'+id;
  const submission=await env.DB.prepare('SELECT * FROM submissions WHERE id=?').bind(key).first<Json>();
  if(!submission)throw new Problem(404,'No saved round belongs to this reviewer.');
  if(submission.undone)return {id,results:[],duplicate:true};
  const rows=await env.DB.prepare("SELECT * FROM events WHERE submission=? AND kind='review'").bind(key).all<Json>();
  const statements:D1PreparedStatement[]=[],results=[];const at=new Date().toISOString();
  for(const r of rows.results){
    const current=await unit(env,r.target);
    if(current.revision!==r.expected_revision+1)throw new Problem(409,'A later review changed this crop. It cannot be undone.');
    const restored={...parse(r.before_data),revision:current.revision+1};
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
  try{await env.DB.batch(statements)}catch(error){if(String(error).includes('review_revision_conflict'))throw new Problem(409,'A later review changed this crop.');throw error}
  return {id,results};
}
// A review's label sits in evidence.label (a round) or evidence.snapshot.character.label (a single
// correction); evidence is itself a JSON string, parsed once. An undo's own evidence is the plain string
// 'undo of <event id>', not JSON, so its label comes from the row's own snapshot column instead — the
// CASE only evaluates the branch for the row's own kind, so a future kind touches neither column.
// This text is repeated verbatim in migration 0011's expression index; keep the two in sync.
export const historyLabelExpr = () => `(CASE kind WHEN 'review' THEN coalesce(json_extract(json_extract(event,'$.evidence'),'$.label'),json_extract(json_extract(event,'$.evidence'),'$.snapshot.character.label')) WHEN 'undo' THEN json_extract(snapshot,'$.character.label') END)`;
// Newest first, keyset-paged on (at,id): `before` is strictly older than that pair, in index order.
export function historyQuery(actor: string | null, label: string | null, cursor: { at: string; id: string } | null): { sql: string; values: (string | number)[] } {
  const where = [`kind IN ('review','undo')`];
  const values: (string | number)[] = [];
  if (actor) { where.push('actor=?'); values.push(actor) }
  if (label !== null) { where.push(`${historyLabelExpr()}=?`); values.push(label) }
  if (cursor) { where.push('(at,id)<(?,?)'); values.push(cursor.at, cursor.id) }
  const sql = `SELECT id,at,actor,target,kind,event,${historyLabelExpr()} AS label FROM events WHERE ${where.join(' AND ')} ORDER BY at DESC,id DESC LIMIT ?`;
  return { sql, values };
}
export function encodeCursor(at: string, id: string): string {
  return btoa(JSON.stringify([at, id]));
}
export function decodeCursor(value: string): { at: string; id: string } {
  try {
    const decoded = JSON.parse(atob(value));
    if (!Array.isArray(decoded) || decoded.length !== 2 || typeof decoded[0] !== 'string' || typeof decoded[1] !== 'string') throw 0;
    return { at: decoded[0], id: decoded[1] };
  } catch { throw new Problem(422, 'Invalid cursor.') }
}
type HistoryRow = { id: string; at: string; actor: string; target: string; kind: string; event: string; label: string | null };
// A review's evidence names its own verdict, issue and correction; an undo's evidence is only the id
// of the event it reverses, so those fields stay null and `undoes` names that event instead.
export function historyItem(row: HistoryRow): Json {
  const undo = row.kind === 'undo';
  const parsedEvent = parse(row.event);
  const evidence = undo ? null : parse(parsedEvent.evidence);
  return {
    id: row.id, at: row.at, actor: row.actor, target: row.target, label: row.label, kind: row.kind as 'review' | 'undo',
    verdict: evidence?.verdict ?? null,
    issue: evidence?.issue ?? null,
    character: evidence?.suggested_character ? literal(evidence.suggested_character) : null,
    reading: evidence?.suggested_reading ?? null,
    round: evidence?.round ?? null,
    batch: evidence?.batch ?? null,
    undoes: undo ? String(parsedEvent.evidence).replace(/^undo of /, '') : null,
  };
}
async function history(env: Env, q: URLSearchParams) {
  const limit = Math.max(1, integer(q, 'limit', 40, 100));
  const actor = q.get('actor') ? text(q.get('actor'), 128, 'actor', true) : null;
  const label = q.get('label') ? text(q.get('label'), 32, 'label', true) : null;
  const before = q.get('before') ? decodeCursor(q.get('before')!) : null;
  const { sql, values } = historyQuery(actor, label, before);
  const rows = await env.DB.prepare(sql).bind(...values, limit + 1).all<HistoryRow>();
  const items = rows.results.slice(0, limit).map(historyItem);
  // The cursor is the last row returned; the next page starts strictly after it.
  const next = rows.results.length > limit ? encodeCursor(rows.results[limit - 1].at, rows.results[limit - 1].id) : null;
  return { items, next };
}
async function reviews(env:Env,all:boolean){
  const rows=await env.DB.prepare(`SELECT e.event,e.snapshot,e.expected_revision,u.revision,u.origin,e.id,u.snapshot AS publication_snapshot,
    EXISTS(SELECT 1 FROM events newer WHERE newer.target=e.target AND newer.expected_revision>e.expected_revision) AS superseded
    FROM events e JOIN units u ON u.id=e.target WHERE e.kind='review' ${all?'':'AND e.processed=0'} ORDER BY e.at`).all<Json>();
  return {version:1,kind:'atlas-character-reviews',reviews:rows.results.map(r=>{
    const event=parse(r.event),snapshot=parse(r.snapshot);
    if(r.origin==='corpus'){const e=parse(event.evidence);return {origin:'corpus',event:{...event,new:{verdict:e.verdict,issue:e.issue,
      character:e.suggested_character?literal(e.suggested_character):null,correction:e.suggested_reading,note:e.note}},
      reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      source_update:{identity:event.target_id,source_revision:snapshot.source_revision,source:snapshot.source,box:snapshot.box,
        original_character:snapshot.source_code_point?literal(snapshot.source_code_point):snapshot.source_label,
        proposed_character:e.suggested_character?literal(e.suggested_character):null,issue:e.issue,applied_upstream:false}}}
    return {event,reviewed:snapshot,current:!r.superseded,current_revision:r.revision,
      expected_revision:r.expected_revision,publication_snapshot:parse(r.publication_snapshot)}
  }),publication:await meta(env,'published_at')};
}
/** Whether a suggestions request names the pixels the unit holds now: a corpus glyph by its source
 *  revision, a crop by its page hash, and a crop published without a hash by its image. */
export function samePixels(q: URLSearchParams, origin: string, data: Json): boolean {
  if (origin === 'corpus') return q.get('source_revision') === data.source_revision;
  return data.image_sha256 ? q.get('image_sha256') === data.image_sha256 : q.get('image') === data.image;
}

export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
      if(request.method==='POST'){
        if(path==='/atlas/corpus/reviews')return json(await submit(env,request,'@corpus'));
        if(path==='/atlas/rounds')return json(await submit(env,request));
        if(path==='/atlas/corrections'){
          // A batch changes many crops at once, so each address is held to a rate.
          const {success}=await env.CORRECTIONS.limit({key:request.headers.get('cf-connecting-ip')??'local'});
          if(!success)throw new Problem(429,'Too many corrections at once. Wait a minute and try again.');
          return json(await submit(env,request,'@batch'));
        }
        const form=path.match(/^\/atlas\/characters\/([^/]+)\/written-form$/);
        if(form||path==='/atlas/corpus/written-forms'){
          // Each save is one small row, and one address is held to a rate as batch corrections are.
          const {success}=await env.WRITTEN_FORMS.limit({key:request.headers.get('cf-connecting-ip')??'local'});
          if(!success)throw new Problem(429,'Too many written forms at once. Wait a minute and try again.');
          return json(await writeForm(env,request,form?decodeURIComponent(form[1]):null));
        }
        const undone=path.match(/^\/atlas\/(?:rounds|corrections)\/([^/]+)\/undo$/);
        if(undone)return json(await undo(env,request,decodeURIComponent(undone[1])));
        const edit=path.match(/^\/(?:atlas\/characters|layers\/units)\/([^/]+)$/);
        if(edit)return json(await submit(env,request,decodeURIComponent(edit[1])));
        const formed=await formsRoute(env,request,path,q,formTools,ctx);
        if(formed)return formed instanceof Response?formed:json(formed);
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path==='/health')return json({ok:true,published_at:await meta(env,'published_at')});
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image)return await media(env,request,image[1],ctx);
      if(path==='/atlas')return json(await catalogue(env,ctx,url));
      if(path==='/atlas/history')return json(await history(env,q));
      if(path==='/atlas/pairs')return json(await pairs(env,ctx,url));
      const pair=path.match(/^\/atlas\/pairs\/([^/]+)$/);
      if(pair)return json(await pairOccurrences(env,url,decodeURIComponent(pair[1])));
      if(path==='/atlas/corpus/characters')return json(await corpusCharacters(env,ctx,url),200,{'cache-control':'private, max-age=300'});
      if(path==='/atlas/corpus/character')return json(record(await unit(env,q.get('id')||'')));
      if(path==='/atlas/collection/status')return json(await meta(env,'collection'));
      const document=path.match(/^\/atlas\/documents\/([^/]+)\/characters$/);
      if(document){let id:string;try{id=decodeURIComponent(document[1])}catch{throw new Problem(404,'No characters are published for this document.')}
        return await documentCharacters(env,url,id,ctx)}
      const visualSample=path.match(/^\/layers\/visual-groups\/samples\/([^/]+)\/image$/);
      if(visualSample){const data=parse((await unit(env,decodeURIComponent(visualSample[1]))).data);
        if(!data.image)throw new Problem(404,'Image not found.');
        return Response.redirect(new URL(data.image,url).href,302)}
      if(path==='/atlas/written-forms'||path==='/atlas/written-forms.json')return json(await writtenForms(env),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-written-forms.json"'}:{});
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviews(env,q.get('include_processed')==='true'),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const similar=path.match(/^\/atlas\/characters\/([^/]+)\/similar$/);
      if(similar){let id:string;try{id=decodeURIComponent(similar[1])}catch{throw new Problem(404,'This character is not in the published collection.')}
        return json(await similarCrops(env,id,integer(q,'limit',12,20),itemsFor))}
      const character=path.match(/^\/atlas\/characters\/([^/]+)(\/suggestions(?:\/context)?)?$/);
      if(character){const row=await unit(env,decodeURIComponent(character[1]));
        if(character[2]){
          const data=parse(row.data);
          if(q.get('revision')!==String(row.revision)||!samePixels(q,row.origin,data))throw new Problem(409,'Character changed.');
          return json(parse(character[2].endsWith('/context')?row.context:row.visual));
        }
        return json(record(row));
      }
      if(path==='/layers/suggest')return json(await suggest(env,q));
      if(path==='/layers/search'){const found=await suggest(env,q);return json({...found,results:found.items,match:found.items[0]||null})}
      const layer=path.match(/^\/layers\/characters\/([^/]+)$/);
      if(layer){const value=decodeURIComponent(layer[1]),{detail}=await known(env,value);const found=await occurrences(env,value,q);
        return json({...detail,variants:await variantsOf(env,ctx,url.origin,detail.char),query:detail.code_point,samples:found.items,occurrences:{...found.counts,filtered:found.total}})}
      if(path==='/layers/occurrences')return json(await occurrences(env,q.get('code_point')||'',q));
      if(path==='/layers/candidates'){const found=await occurrences(env,q.get('code_point')||'',q,'corpus');
        return json({...found,glyphs:found.total,glyph_items:found.items,retry:false})}
      if(path==='/layers/gallery')return json(await gallery(env,q));
      if(path==='/layers/summary')return json(await meta(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures'){
        const selector=path.endsWith('ligatures')?"json_extract(data,'$.ligature') IS NOT NULL":"json_array_length(json_extract(data,'$.grapheme.members'))>1";
        const rows=await env.DB.prepare(`SELECT data FROM characters WHERE ${selector} LIMIT ? OFFSET ?`).bind(integer(q,'limit',200,500),integer(q,'offset',0)).all<{data:string}>();
        return json({items:rows.results.map(r=>parse(r.data)),total:rows.results.length})}
      if(path==='/atlas/corpus/reviews'){
        const rows=await env.DB.prepare("SELECT * FROM units WHERE origin='corpus' AND state='flagged' ORDER BY id LIMIT 96").all<UnitRow>();
        return json({items:rows.results.map(compact),total:rows.results.length})}
      if(path.startsWith('/atlas/forms/')){const formed=await formsRoute(env,request,path,q,formTools,ctx);
        if(formed)return formed instanceof Response?formed:json(formed)}
      throw new Problem(404,'Unknown endpoint.');
    }catch(error){
      const open=path.startsWith('/atlas/documents/')?OPEN:{};
      if(error instanceof Problem)return json({detail:error.message,...error.extra},error.status,open);
      console.error(JSON.stringify({event:'request_failed',path,error:error instanceof Error?error.name:'unknown'}));
      return json({detail:'The request could not be completed. Please retry.'},503,open);
    }
  },
} satisfies ExportedHandler<Env>;
