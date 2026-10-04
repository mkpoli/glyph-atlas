// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
import { ROUND_MAX } from './rounds';
import { formsRoute, withForm, formed, FORM_COLUMNS, type FormTools, type UnitForm } from './forms';
import { similarCrops } from './similar';
import { MAX_BODY, QueryError, addressKey, boundedText, modelFile, modelInfo, query as imageQuery } from './reverse';
import { componentSearch, componentTerm } from './components';
import { formsFor, setForm, withForms } from './cropForms';
import { formProblem, isSequence } from './representation';
import { auth, claim, owned, providers, viewer } from './auth';
import { AVATAR_PATH, avatar, setAvatar } from './avatar';
import { ranking } from './ranking';
import { connections } from './connections';
import { reviewers, submissions } from './admin';
import { READ_BUDGET, RETRY_AFTER, described, retried, transient } from './busy';
import { actOnClaim, claimsOf, ledgerPage, writeClaim, type LedgerTools } from './ledger';
import { YEAR_KEY, dateStats, datingJoin, datingOf, decadeColumn, documentDates, documentOf, withDating, yearCondition, yearOptions, yearOrder, type YearOptions } from './dating';
export { leastTypicalQuery } from './forms';
export { componentMatchQuery } from './components';
export { dateClaimsQuery, dateStatsQuery, datingQuery, tallyDates } from './dating';
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
// A well-formed ideographic description sequence describes one Han character Unicode lacks.
const idsCharacter = (value: string) => isSequence(value) && formProblem(value) === null;
export const categoryOf=(value:string)=>{const first=[...value][0]??'';if(idsCharacter(value))return 'kanji';return /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(first)?'kana':/\p{Script=Han}/u.test(first)?'kanji':/\p{Script=Hangul}/u.test(first)?'hangul':isGugyeol(first)?'gugyeol':'other'};
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
  return env.DB.prepare(`INSERT OR IGNORE INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,
    data,snapshot,context,visual,document,style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
    .bind(row.id,'corpus',d.written_character||null,d.grapheme||(d.written_character?cp(d.written_character):null),d.visual_group?.id||null,row.fresh.production,
      row.category||categoryOf(d.label),d.state,d.revision,row.quiz,1,row.fresh.shuffle,row.data,row.snapshot,row.context,row.visual,null,row.fresh.style);
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
  const forms = await formsFor(env, [...found.keys()]);
  for (const [id, item] of found) item.form = forms.get(id) ?? null;
  const rest = ids.filter(id => !found.has(id));
  if (rest.length) {
    const pointers = await env.DB.prepare(`SELECT * FROM corpus_units WHERE id IN (${marks(rest.length)})`)
      .bind(...rest).all<CorpusRow>();
    const records = await Promise.all(pointers.results.map(p => corpusData(env, p).then(d => [p.id, d] as const, () => null)));
    for (const record of records) if (record) found.set(record[0], { ...listing(record[1]), origin: 'corpus' });
  }
  const dating = await datingOf(env, [...found.values()].map(documentOf));
  for (const [id, item] of found) found.set(id, { ...item, dating: dating.get(documentOf(item) ?? '') ?? {} });
  return found;
}
function compact(row: UnitRow): Json {
  return { ...listing(parse(row.data)), ...(row.document ? { document: row.document } : {}), ...(row.style ? { style: row.style } : {}) };
}
// A crop's record with its evidence version (0047).
const record = (row: UnitRow): Json => {
  const data = parse(row.data);
  return { ...data, crop_version: row.crop_version ?? cropVersion(row.id, data), crop_editable: row.origin !== 'corpus' && Boolean(redrawLimits(data)) };
};
// A crop's evidence version as `units.crop_version` computes it, for a corpus glyph that has no row yet:
// its id, its image checksum and its box in whole pixels; a box of anything else has none. JSON.parse
// reads a whole number written as a real (`1.0`) as an integer, which SQLite does not, so a claim names
// the version its row gives once the row is written.
export function cropVersion(id: string, data: Json): string | null {
  const pixels = data.image_sha256 ?? data.source_revision ?? null;
  if (typeof pixels !== 'string') return null;
  const box = data.box;
  if (box == null) return `${id}@${pixels}@`;
  const values = ['x', 'y', 'w', 'h'].map(key => box[key]);
  return values.every(Number.isSafeInteger) ? `${id}@${pixels}@${values.join(',')}` : null;
}
// Every evidence version a crop has had here, oldest first (0047); `current` is the one it has now.
// One range of `crop_version_unit`, and a crop recut more often than this lists its first ones.
const VERSIONS_LISTED = 200;
export const cropVersionsQuery = () => `SELECT id,pixels,box,document,image,at FROM crop_versions WHERE unit=? ORDER BY at,id LIMIT ${VERSIONS_LISTED}`;
async function cropVersions(env: Env, id: string) {
  const row = await unit(env, id);
  const versions = (await env.DB.prepare(cropVersionsQuery()).bind(row.id).all<Json>()).results;
  return { id: row.id, current: record(row).crop_version, versions };
}
// A crop's record for its inspector, with its form, its book's dates and the claims they rest on.
const inspected = async (env: Env, row: UnitRow): Promise<Json> => {
  const found = (await withForms(env, [record(row)]))[0];
  return { ...found, ...await documentDates(env, row.document ?? found.source?.document_id ?? null) };
};
// The page rectangle a reviewer may redraw a local crop's box in, in page pixels: the context the
// inspector shows, which lies inside the page. A corpus glyph's box belongs to its source, so it has none.
export function redrawLimits(data: Json): { x: number; y: number; w: number; h: number } | null {
  const c = data?.context_box, s = data?.source_scale;
  if (!data?.context || !c || !data.crop_box || !Array.isArray(s) || !(s[0] > 0) || !(s[1] > 0)) return null;
  return { x: Math.floor(c.x / s[0]), y: Math.floor(c.y / s[1]), w: Math.ceil((c.x + c.w) / s[0]) - Math.floor(c.x / s[0]), h: Math.ceil((c.y + c.h) / s[1]) - Math.floor(c.y / s[1]) };
}
// A redrawn box as the inspector sends it: whole page pixels, at least two each way, inside the page view.
export function redrawnBox(value: Json, data: Json): { x: number; y: number; w: number; h: number } {
  const limits = redrawLimits(data);
  if (!limits) throw new Problem(422, 'This crop cannot be redrawn.');
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join() !== 'h,w,x,y' || !['x', 'y', 'w', 'h'].every(k => Number.isSafeInteger(value[k])))
    throw new Problem(422, 'A box is four whole pixels: x, y, w, h.');
  const { x, y, w, h } = value;
  if (w < 2 || h < 2 || x < limits.x || y < limits.y || x + w > limits.x + limits.w || y + h > limits.y + limits.h)
    throw new Problem(422, 'The crop must stay inside the source image.');
  return { x, y, w, h };
}
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
// A reviewer's own skip that still rests, under any id they have written as: at the crop's current box, from a round not undone.
const OWN_SKIP = (reviewer: string, since: string) => `EXISTS(SELECT 1 ${SKIPS} AND k.actor IN ${owned(reviewer)} AND k.at>${quoted(since)})`;
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
  for (const crop of crops) {
    text(crop?.id, 512, 'crop id', true);
    // A batch names a character only; a crop is redrawn one at a time, in its own review.
    if (crop.box !== undefined) throw new Problem(422, 'A correction of many crops cannot redraw one.');
  }
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
// The grapheme each corpus character is filed under, from the browser's cached counts: a character
// the collection holds only as corpus glyphs (𛂞) joins its family's round (は's) in the round menu.
async function corpusGraphemes(env: Env, ctx: ExecutionContext, origin: string) {
  const { items } = await corpusCharacters(env, ctx, new URL(`${origin}/atlas/corpus/characters?production=all`)) as { items: [string, string, number][] };
  return new Map(items.map(([label, grapheme]) => [label, grapheme]));
}
export const GRAPHEME_KEY = /^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*$/;
// A grapheme key (`U+306F`) as the text it names.
export const graphemeText = (key: string) => key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('');
// A grapheme key as a request names it, upper-cased with single spaces, or null when none is named.
export function graphemeKey(value: string | null | undefined): string | null {
  const key = text(value, 256, 'grapheme')?.toUpperCase().split(/\s+/).join(' ');
  if (!key) return null;
  if (!GRAPHEME_KEY.test(key) || key.split(' ').some(p => parseInt(p.slice(2), 16) > 0x10FFFF)) throw new Problem(422, 'Invalid grapheme.');
  return key;
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
// The crops a listing starts from: local ones, those a round may deal, in the material asked for.
// A round and its reference strips name their grapheme's characters, and read each through
// `unit_character`: the review filter is kept off its index (`+`), which would otherwise drive the
// query over every crop that can be dealt.
export function listingFilter(review: boolean, production: string, characters: string[] | null) {
  const [materials, materialValues] = material(production, 'production');
  const where = ["origin='local'", ...(review ? [characters === null ? 'quiz=1' : '+quiz=1'] : []), materials];
  const values: (string | number)[] = [...materialValues];
  if (characters !== null) { where.push('character IN (SELECT value FROM json_each(?))'); values.push(JSON.stringify(characters)) }
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
    WHERE id IN (SELECT target FROM skips WHERE actor IN ${owned(reviewer)} AND at>${quoted(since)}) AND ${filter}
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
// The pair and trigram frequencies Explore's grid shows: runs of crops that follow each other on a
// line (`unit_ngrams`), counted by the text their labels make, most frequent first. The whole
// collection's count reads every run of the length asked for and a book's reads its own, through the
// index that also groups them; the edge keeps one copy per catalogue version, length and book. A run is
// shown down the page where most of its occurrences are written that way.
const NGRAMS_MAX = 480, NGRAM_SIZES = new Set(['2', '3']);
function ngramSize(value: string) {
  if (!NGRAM_SIZES.has(value)) throw new Problem(404, 'Runs of two or three characters are counted.');
  return Number(value);
}
export function ngramsQuery(document: boolean) {
  return `SELECT text,count(*) AS n,2*sum(vertical)>=count(*) AS vertical FROM unit_ngrams
    WHERE ${document ? 'document=? AND ' : ''}size=? AND text IS NOT NULL GROUP BY text ORDER BY n DESC,text LIMIT ${NGRAMS_MAX}`;
}
async function ngrams(env: Env, ctx: ExecutionContext, url: URL, size: number) {
  const document = text(url.searchParams.get('document'), 256, 'document');
  const key = new Request(`${url.origin}/atlas/ngrams/${size}?document=${encodeURIComponent(document ?? '')}&v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const rows = await env.DB.prepare(ngramsQuery(Boolean(document))).bind(...(document ? [document] : []), size).all<{ text: string; n: number; vertical: number }>();
  const body = { items: rows.results.map(row => ({ ...row, vertical: Boolean(row.vertical) })), limit: NGRAMS_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
// One run's occurrences: its crops in reading order, each with its box on the page, whether their line
// is written down the page, and the page around them (`ngramPage`); the run as a whole is written the way
// most of its occurrences are. They come in the order the run's index keeps (by the first crop's id), each crop
// found by its key; a trigram's third is joined only when there is one. A book's are read through the
// index it shares with the count. The join order is fixed and the origin test kept off its index (`+`):
// the planner would otherwise start from every local crop.
const NGRAM_PAGE_MAX = 96, NGRAM_OFFSET_MAX = 2000;
const NGRAM_CROPS = `FROM unit_ngrams p
    CROSS JOIN units a ON a.id=p.first AND +a.origin='local' CROSS JOIN units b ON b.id=p.second AND +b.origin='local'
    LEFT JOIN units c ON c.id=p.third AND +c.origin='local'`;
export function ngramOccurrencesQuery(document: boolean) {
  return `SELECT a.data AS first, b.data AS second, c.data AS third, a.document AS document, p.vertical ${NGRAM_CROPS}
    WHERE ${document ? 'p.document=? AND ' : ''}p.size=? AND p.text=? AND (p.third IS NULL OR c.id IS NOT NULL) ORDER BY p.first LIMIT ? OFFSET ?`;
}
export function ngramCountQuery(document: boolean) {
  return `SELECT count(*) AS n, sum(p.vertical) AS vertical ${NGRAM_CROPS}
    WHERE ${document ? 'p.document=? AND ' : ''}p.size=? AND p.text=? AND (p.third IS NULL OR c.id IS NOT NULL)`;
}
// The page around a run, from one of its crops' context renders: the box holding every crop, with a
// margin of a fifth of the largest, clipped to the render. A render shows the page a few characters
// around its own crop, so it nearly always holds the whole run; the smallest that does is the sharpest.
// Null when a crop has no box or no render holds them all: the run's crops are then laid out apart.
type Rect = { x: number; y: number; w: number; h: number };
export function ngramPage(crops: Json[]): { image: string; box: Rect; region: Rect } | null {
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
  return { image: render.context_image, box,
    region: { x, y, w: Math.min(box.x + box.w, right + margin) - x, h: Math.min(box.y + box.h, bottom + margin) - y } };
}
async function ngramOccurrences(env: Env, url: URL, size: number, run: string) {
  const q = url.searchParams;
  const value = text(run, 96, 'text', true)!;
  const document = text(q.get('document'), 256, 'document');
  const limit = integer(q, 'limit', 48, NGRAM_PAGE_MAX), offset = integer(q, 'offset', 0);
  if (offset > NGRAM_OFFSET_MAX) throw new Problem(404, 'A run does not page this far.');
  const bound = [...(document ? [document] : []), size, value];
  const [count, page] = await env.DB.batch([
    env.DB.prepare(ngramCountQuery(Boolean(document))).bind(...bound),
    env.DB.prepare(ngramOccurrencesQuery(Boolean(document))).bind(...bound, limit, offset),
  ]) as D1Result<any>[];
  const found = page.results as { first: string; second: string; third: string | null; document: string | null; vertical: number }[];
  // The crops of one run stand on one page, so they share their document's dates.
  const dating = await datingOf(env, found.map(row => row.document));
  const items = found.map(row => {
    const crops = [row.first, row.second, row.third].filter(Boolean).map(data => parse(data!));
    const dated = row.document ? dating.get(row.document) : undefined;
    return { crops: crops.map(c => ({ ...listing(c), crop_box: c.crop_box ?? null, dating: dated ?? {} })),
      vertical: Boolean(row.vertical), page: ngramPage(crops) };
  });
  const { n: total, vertical } = count.results[0] as { n: number; vertical: number | null };
  return { text: value, size, document, total, vertical: 2 * (vertical ?? 0) >= total, next_offset: offset + items.length, items };
}
async function catalogue(env: Env, ctx: ExecutionContext, url: URL, reviewer: string | null) {
  const q = url.searchParams;
  const purpose = q.get('purpose') || 'browse';
  const production = q.get('production') || (purpose === 'review' ? REVIEW_SCOPE : 'all');
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const review = purpose === 'review';
  const seed = integer(q, 'seed', 0, 2147483647);
  const limit = integer(q, 'limit', 60, 96), offset = integer(q, 'offset', 0);
  if (review && offset > ROUND_OFFSET_MAX) throw new Problem(404, 'A round does not page this far.');
  // One rest window for the counts and the listing.
  const since = restSince(), state = stateFor(reviewer, since);
  // An empty `character` names none. A round names its grapheme, and deals every character of it.
  const character = q.get('character') || null;
  const grapheme = graphemeKey(q.get('grapheme'));
  const scoped = review && grapheme !== null;
  const members = scoped ? await graphemeMembers(env, grapheme) : null;
  const { where, values } = listingFilter(review, production, members);
  const [materials, materialValues] = material(production, 'production');
  const queries = facetsQueries(review, reviewer, where, scoped, since);
  const [stored, marked, skipped] = [queries.stored, queries.marked, queries.skipped].map(sql => sql ? env.DB.prepare(sql).bind(...values) : null);
  // Only counts that are the same for every visitor are cached; a reviewer's own skips are theirs.
  let groups: Facet[], published: D1Result<{ label: string; n: number }> | null = null;
  if (review) {
    const corpus = corpusCountQuery(production, members);
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
  // A corpus glyph nothing has named is pending for everyone, and the table counts them per character,
  // each under the grapheme its family files it under.
  const corpus = new Map<string, number>();
  const families = published && !scoped ? await corpusGraphemes(env, ctx, url.origin) : null;
  if (published) for (const row of published.results) if (row.n > 0) {
    corpus.set(row.label, row.n); add(row.label, 'pending', row.n, null, null, scoped ? grapheme : families!.get(row.label) ?? null)
  }
  // Browse's counts are grouped by character, book and state, so a browse listing filtered by those
  // alone takes its total from them; counting it again would read every crop it holds on each page.
  // Any other filter clears this, and the listing is counted.
  let tally: ((row: Facet) => boolean)[] | null = review ? null : [];
  if (character) { where.push('character=?'); values.push(character); tally?.push(row => row.label === character) }
  const document = text(q.get('document'), 256, 'document');
  if (document) { where.push('document=?'); values.push(document); tally?.push(row => row.document === document) }
  // A grapheme is a family's representative code point, or a label's own code points. Every named crop
  // has a family (its own code points when the character table gives none), so browsing one is one
  // lookup that `unit_family_sample` serves in shuffle order.
  if (grapheme && !scoped) { where.push('family=?'); values.push(grapheme); tally = null }
  // A search finds a local crop by its written character, one range of `unit_character`. Typed kana
  // such as トモ reach a character through the alias index first (`/atlas/suggest`).
  if (q.get('q')) {
    where.push('character=?');
    values.push(literal(q.get('q')!)); tally = null;
  }
  // With a character, a grapheme or a search named, its own index finds the few crops and the script only filters
  // them (`+`); the script's index would read every crop of that script.
  if (q.get('group') && q.get('group') !== 'all') { where.push(character || grapheme || q.get('q') ? '+category=?' : 'category=?'); values.push(q.get('group')!); tally = null }
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
  // A grapheme's round deals its named and then its untouched corpus glyphs after its local crops.
  // Corpus glyphs belong to no work of the collection, so a round narrowed to one work deals none.
  const dealt = scoped && !character && !document && ['all', 'pending'].includes(q.get('state') || 'all') && !q.get('q')
    && (!q.get('group') || q.get('group') === 'all' || members!.every(member => categoryOf(member) === q.get('group')));
  const named = dealt ? { sql: namedRoundQuery(materials, state), values: [JSON.stringify(members), ...materialValues] } : null;
  const from = named ? `(SELECT * FROM units WHERE ${where.join(' AND ')} UNION ALL ${named.sql}) AS units` : `units WHERE ${where.join(' AND ')}`;
  const fromValues = named ? [...values, ...named.values] : values;
  // A crop another reviewer skipped comes first in a round: it needs a second pair of eyes. Then a
  // character's local crops, then its named corpus glyphs.
  const others = review ? `EXISTS(SELECT 1 ${SKIPS}${reviewer ? ` AND k.actor NOT IN ${owned(reviewer)}` : ''}) DESC,origin='corpus',` : '';
  const order = others + (review && seed % 5 ? 'priority,' : '');
  // Flagged view: a crop already looked at in the inspector queues behind the ones nobody has reviewed yet.
  const reviewedLast = flaggedView ? `${REVIEWED_IN_INSPECTOR},` : '';
  const columns = `*,${state} AS effective,(SELECT shape_order FROM unit_shapes s WHERE s.id=units.id) AS shape_order,${SUSPECT} AS suspect`;
  // Browse deals crops in `shuffle` order from a point the seed picks, and wraps round past the
  // highest: an index serves that order, where a seed-scrambled order sorts every row on each visit.
  // A named character is found through `unit_character` instead, and its few crops sort in memory.
  const rotated = !review && !flaggedView && !character && !q.get('q');
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
  const items: Json[] = await withForms(env, rows
    .map(row => { const item = compact(row); return { ...item, state: row.effective, shape_order: row.shape_order,
      suspect: suspectOf(row.suspect ? parse(row.suspect) as Suspect : null, item) } }));
  // Positions run through the `units` rows and then the untouched corpus glyphs. A glyph its record
  // keeps out still takes its position, so `next_offset` can run ahead of the items, and once the
  // glyphs run out `total` is what was there to deal.
  let total = listed, next = offset + items.length;
  const unnamed = dealt ? members!.reduce((n, member) => n + (corpus.get(member) || 0), 0) : 0;
  if (dealt && unnamed) {
    total += unnamed;
    if (items.length < limit) {
      const round = await corpusRound(env, members!, production, seed, Math.max(offset - listed, 0), limit - items.length);
      // A glyph is one crop whichever list deals it, even should its published row read as untouched.
      const shown = new Set(items.map(item => item.id));
      items.push(...round.items.filter(item => !shown.has(item.id))); next += round.read;
      if (round.exhausted) total = next;
    }
  }
  return { total, next_offset: next, available: Object.values(counts).reduce((a:number,b:any) => a+b,0),
    counts, purpose, production, review_limit:ROUND_MAX, review_epoch: await meta(env, 'review_epoch') || 0, query: q.get('q'),
    ...(scoped ? { grapheme: { code_point: grapheme, char: graphemeText(grapheme), members } } : {}),
    categories: [...categories.values()].sort((a,b) => b.total-a.total || a.label.localeCompare(b.label)),
    documents: [...documents.values()].sort((a,b) => b.total-a.total || (a.title ?? '').localeCompare(b.title ?? '') || a.id.localeCompare(b.id)),
    reported_count: reportedCount ? (reportedCount.results[0] as { n: number }).n : 0,
    items: await withDating(env, items) };
}
// Untouched assigned corpus glyphs per character in this material, of every character or of a grapheme's.
export function corpusCountQuery(production: string, characters: string[] | null = null) {
  const [materials, values] = material(production, 'production');
  return { sql: `SELECT character AS label,sum(n-named) AS n FROM corpus_characters WHERE ${characters === null ? '' : 'character IN (SELECT value FROM json_each(?)) AND '}${materials} GROUP BY character`,
    values: characters === null ? values : [JSON.stringify(characters), ...values] };
}
// The named corpus glyphs of a round's characters that are due, from each one's most recently named
// pending rows.
export function namedRoundQuery(materials: string, state: string) {
  return `SELECT * FROM (SELECT u.* FROM json_each(?) m JOIN units u ON u.rowid IN
    (SELECT rowid FROM units WHERE origin='corpus' AND character=m.value AND state='pending' ORDER BY rowid DESC LIMIT ${NAMED_WINDOW})) AS units
    WHERE quiz=1 AND ${materials} AND ${state}='pending'`;
}
// One character's untouched corpus glyphs of one material, or of every material, in shuffle order
// from a point the seed picks.
export function corpusRoundQuery(production: string | null, side: '>=' | '<') {
  return `SELECT * FROM corpus_units WHERE character=?${production ? ' AND production=?' : ''} AND named=0 AND shuffle${side}?
    ORDER BY shuffle LIMIT ?`;
}
// The glyphs wrap round from the seeded point, so each seed deals a different but stable order that
// the index serves as it stands. Each character of the grapheme, and in a scope short of `all` each
// production a character holds in it, is read through its own index range, and they are merged.
async function corpusRound(env: Env, characters: string[], production: string, seed: number, offset: number, limit: number) {
  const start = seed % SHUFFLE_RANGE, wanted = offset + limit;
  const held = (await env.DB.prepare('SELECT character,production FROM corpus_characters WHERE character IN (SELECT value FROM json_each(?))')
    .bind(JSON.stringify(characters)).all<{ character: string; production: string }>()).results;
  const ranges = production === 'all' ? [...new Set(held.map(row => row.character))].map(character => ({ character, kind: null as string | null }))
    : held.filter(row => inMaterial(production, row.production)).map(row => ({ character: row.character, kind: row.production as string | null }));
  if (!ranges.length) return { items: [], read: 0, exhausted: true };
  const page = async (side: '>=' | '<', n: number) => {
    const results = await env.DB.batch(ranges.map(({ character, kind }) => env.DB.prepare(corpusRoundQuery(kind, side))
      .bind(character, ...(kind ? [kind] : []), start, n)));
    return results.flatMap(r => r.results as CorpusRow[]).sort((a, b) => a.shuffle - b.shuffle || (a.id < b.id ? -1 : 1)).slice(0, n);
  };
  const rows = await page('>=', wanted);
  if (rows.length < wanted) rows.push(...await page('<', wanted - rows.length));
  const read = rows.slice(offset), items: Json[] = [];
  // Bound simultaneous R2 streams, as for a corpus search page.
  for (const batch of chunks(read, 8)) {
    const records = await Promise.all(batch.map(row => corpusData(env, row)));
    for (const [i, data] of records.entries()) {
      // The record decides: a glyph whose image this site may not serve, or whose record disagrees
      // with its published row about the character or the material, is not dealt.
      if (dealable('corpus', data) && data.label === batch[i].character && inMaterial(production, productionOf(data)))
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
export const gallerySampleQuery = (side: '>=' | '<') => `SELECT s.data,u.data AS current,${FORM_COLUMNS.split(',').map(c => `f.${c}`).join(',')}
  FROM corpus_gallery s JOIN corpus_units c ON c.id=s.id AND c.object=s.object AND c.offset=s.offset
  LEFT JOIN units u ON u.id=s.id LEFT JOIN form_units f ON f.id=s.id
  WHERE s.shuffle${side}? ORDER BY s.shuffle LIMIT ?`;
async function gallery(env: Env, q: URLSearchParams) {
  const limit = integer(q, 'limit', 24, 96), start = integer(q, 'seed', 0, 2147483647) % SAMPLE_RANGE;
  type Row = UnitForm & { data: string; current: string | null };
  const rows = (await env.DB.prepare(gallerySampleQuery('>=')).bind(start, limit).all<Row>()).results;
  if (rows.length < limit) rows.push(...(await env.DB.prepare(gallerySampleQuery('<')).bind(start, limit - rows.length).all<Row>()).results);
  const items = await withForms(env, rows.map(r => r.current ? parse(r.current) : formed(parse(r.data), r.id ? r : null, formTools)));
  return { status: 'ok', available: items.length, items: await withDating(env, items) };
}
function chunks<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}
// A character's edges in the 異体字 graph, both ways (0033): each by the key or by `b`'s index.
export const variantEdgesQuery = () => `SELECT b AS other,relation,source,detail,widens FROM character_variants WHERE a=?
  UNION ALL SELECT a AS other,relation,source,detail,widens FROM character_variants WHERE b=? LIMIT 2000`;
// A character's derived list (0046), in the order and caps refs.derived_variants gives it: one key range.
export const derivedEdgesQuery = () => `SELECT b AS other,subs FROM character_derived WHERE a=? ORDER BY rank`;
// What the substitutions a derived list came by are backed by: each one's count and every attesting
// pair with the sources that state it (the `component_variants` rows, keyed by substitution), at most
// SUBSTITUTIONS_READ at a time so no statement binds more than D1's hundred parameters.
export const substitutionQuery = (n: number) => `SELECT a,b,count,pairs FROM component_variants WHERE ${
  Array(n).fill('(a=? AND b=?)').join(' OR ')}`;
const SUBSTITUTIONS_READ = 50;
// How many crops each of a bounded list of characters has here and in the corpus, from the counts the
// triggers keep (0032, 0006): one key range per character, never a scan of the crops.
export const variantCountsQuery = (n: number) => `SELECT character,sum(n) AS n FROM unit_counts WHERE origin='local' AND character IN (${Array(n).fill('?').join(',')}) GROUP BY character`;
export const variantCorpusCountsQuery = (n: number) => `SELECT character,sum(n) AS n FROM corpus_characters WHERE character IN (${Array(n).fill('?').join(',')}) GROUP BY character`;
// Each row of a card's variants lists at most this many, the most attested first; a gallery widens to
// exactly the first row. The Python layer's VARIANTS_SHOWN.
const VARIANTS_SHOWN = 32;
// The tier of predictions, as its own source id: the citation comes from the export's metadata.
const DERIVED_IDS = 'derived-ids';
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
  return `SELECT c.*,c.${field} AS k,c.style_order AS s,c.id AS i,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
    FROM corpus_units c LEFT JOIN units u ON c.id=u.id
    WHERE c.${field}${list} AND (u.id IS NULL OR u.${field}=c.${field})
    UNION ALL SELECT c.*,u.${field} AS k,u.style_order AS s,u.id AS i,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
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
  const derivedRows = (await env.DB.prepare(derivedEdgesQuery()).bind(char).all<{ other: string; subs: string }>()).results;
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
  const derived = await derivedOf(env, derivedRows);
  const chars = [...shown.map(entry => entry.char), ...derived.filter(entry => entry.encoded).map(entry => entry.char)];
  const [local, corpus] = chars.length ? await env.DB.batch([
    env.DB.prepare(variantCountsQuery(chars.length)).bind(...chars),
    env.DB.prepare(variantCorpusCountsQuery(chars.length)).bind(...chars),
  ]) as D1Result<{ character: string; n: number }>[] : [null, null];
  const count = (rows: { character: string; n: number }[] | undefined) => new Map((rows ?? []).map(row => [row.character, row.n]));
  const here = count(local?.results), there = count(corpus?.results);
  const cited: Record<string, string> = (await meta(env, 'variant_sources')) || {};
  const rows = shown.map(entry => ({ ...entry, sources: [...new Set(entry.relations.map(r => r.source))].sort(),
    count: here.get(entry.char) ?? 0, corpus_count: there.get(entry.char) ?? 0 }));
  const predicted = derived.map(entry => ({ ...entry,
    count: here.get(entry.char) ?? 0, corpus_count: there.get(entry.char) ?? 0 }));
  const used = new Set(rows.flatMap(row => row.sources));
  for (const entry of predicted) for (const source of entry.sources) used.add(source);
  if (predicted.length) used.add(DERIVED_IDS);
  const found = { items: rows.filter(row => row.widens), related: rows.filter(row => !row.widens), derived: predicted,
    total: byChar.size, sources: Object.fromEntries([...used].sort().map(source => [source, cited[source] ?? source])) };
  ctx.waitUntil(caches.default.put(key, Response.json(found, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return found;
}
type DerivedEntry = { char: string; code_point: string | null; encoded: boolean;
  substitutions: { was: string; became: string; count: number; pairs: { a: string; b: string; sources: string[] }[] }[];
  sources: string[] };
// The derived group of one character as the export ranked it, each row's substitutions backed by
// their `component_variants` rows. A form of one character is encoded, a sequence is not.
async function derivedOf(env: Env, rows: { other: string; subs: string }[]): Promise<DerivedEntry[]> {
  const listed = rows.map(row => ({ other: row.other, subs: JSON.parse(row.subs) as [string, string][] }))
  const keys = [...new Map(listed.flatMap(row => row.subs).map(sub => [sub.join('\u0000'), sub])).values()];
  const backed = keys.length ? (await env.DB.batch(chunks(keys, SUBSTITUTIONS_READ).map(part =>
    env.DB.prepare(substitutionQuery(part.length)).bind(...part.flat()))) as D1Result<{ a: string; b: string; count: number; pairs: string }>[])
    .flatMap(result => result.results) : [];
  const evidence = new Map(backed.map(row => [`${row.a}\u0000${row.b}`, row]));
  return listed.flatMap(row => {
    const substitutions = row.subs.flatMap(([was, became]) => {
      const found = evidence.get(`${was}\u0000${became}`);
      return found ? [{ was, became, count: found.count, pairs: JSON.parse(found.pairs) as DerivedEntry['substitutions'][0]['pairs'] }] : [];
    });
    if (!substitutions.length) return [];
    const encoded = [...row.other].length === 1;
    return [{ char: row.other, code_point: encoded ? cp(row.other) : null, encoded, substitutions,
      sources: [...new Set(substitutions.flatMap(sub => sub.pairs.flatMap(pair => pair.sources)))].sort() }];
  });
}
async function known(env: Env, value: string) {
  const key = cp(literal(value));
  const row = await env.DB.prepare('SELECT data,detail FROM characters WHERE code_point=?').bind(key).first<{data:string;detail:string}>();
  if (row) return { data: parse(row.data), detail: parse(row.detail) };
  if (idsCharacter(literal(value))) return describedCard(literal(value));
  throw new Problem(404, 'Character not found.');
}

/** The card of a character Unicode lacks, written as an ideographic description sequence: it has no
 * entry in the character table and is its own grapheme, in the shape the table's cards have. */
export function describedCard(value: string) {
  const code_point = cp(value), url = '/layers/characters/' + code_point;
  const self = { code_point, char: value, name: null, script: 'han' };
  const grapheme = { ...self, label: value, members: [{ code_point, char: value }], character_count: 1, relation: 'self',
    evidence: [], url: '/layers/graphemes/' + code_point, is_self: true, occurrence_count: 0 };
  const data = { ...self, age: null, block: null, readings: [], reading: null, jibo: [], ligature: null, occurrence_count: 0,
    url, grapheme, default_scope: 'character', kind: 'han',
    candidates: { status: 'ok', known: false, glyphs: 0, lines: 0, pages: 0, total: 0, sources: [],
      counts_kind: 'source_transcription_classes', requires_family_scope: false, source_glyphs: 0, family_glyphs: 0, imported: 0 } };
  const detail = { ...data, alias: null, category: null, confusables: [], characters: [data], derived: [], expansions: [],
    visual_analysis: { status: 'not_analyzed', family: code_point, model_revision: null, sample_count: 0, assigned_count: 0,
      unassigned_count: 0, groups: [] } };
  return { data, detail };
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
      total, capped: counted > WIDENED_CAP, available: rows.results.length, items: await withDating(env, await withForms(env, (rows.results as UnitRow[]).map(compact))),
      counts: { total, exact: total, exact_total: total }, style_groups: STYLE_NAMES, scope: 'variants', status: 'ok' };
  }
  let counted: D1PreparedStatement, listed: D1PreparedStatement;
  const dated = yearOptions(q, (status, message) => { throw new Problem(status, message) });
  if (dated) {
    // Placed or narrowed by date: each crop joins its book's date by key, and the page is sorted here.
    const grapheme = q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme';
    const family = data.grapheme?.code_point || data.code_point;
    const { sql: cond, values: years } = yearCondition(dated.years);
    const from = `FROM units u${datingJoin('u.document', dated.axis)} WHERE ${grapheme
      ? 'u.id IN (SELECT id FROM units WHERE origin=? AND family=? UNION SELECT id FROM units WHERE origin=? AND character=?)' : 'u.origin=? AND u.character=?'}`;
    const keys = grapheme ? [origin, family, origin, data.char] : [origin, data.char];
    counted = env.DB.prepare(`SELECT u.style_order AS s,count(*) AS n ${from}${tail}${cond} GROUP BY 1`).bind(...keys, ...extra, ...years);
    // The page is found by row and sort key alone, and only its own rows are read whole: a crop's
    // JSON is kilobytes, and sorting it with every crop of the character costs more the further it pages.
    listed = env.DB.prepare(datedCropsQuery(from, styled + cond, dated.order)).bind(...keys, ...styledExtra, ...years, limit, offset);
  } else if (q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme') {
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
    total, available: rows.results.length, items: await withDating(env, await withForms(env, (rows.results as UnitRow[]).map(compact))),
    counts:{ total, exact:total, exact_total:total }, styles, style_groups: STYLE_NAMES, scope:q.get('scope') || 'character', status:'ok',
    ...(dated ? { order: dated.order, axis: dated.axis, years: dated.years } : {}) };
}
// A page of crops placed by date: their rows and sort keys, then the rows themselves, in that order.
export const datedCropsQuery = (from: string, conditions: string, order: 'style' | 'year') =>
  `SELECT u.* FROM (SELECT u.rowid AS r,${YEAR_KEY} AS y,coalesce(d.end,d.start) AS z,u.style_order AS so,u.id AS i ${from}${conditions}
    ORDER BY ${order === 'year' ? 'y IS NULL,y,z,so,i' : 'so,i'} LIMIT ? OFFSET ?) k CROSS JOIN units u ON u.rowid=k.r
    ORDER BY ${order === 'year' ? 'k.y IS NULL,k.y,k.z,k.so,k.i' : 'k.so,k.i'}`;
// The stamps the decades and the time axis are kept at the edge by: a publication or refresh of the
// crops, a recount of the corpus, the corpus glyphs' documents and the dates. A review moves a crop to
// another character only until the hour is out.
async function axisVersion(env: Env): Promise<string> {
  const stamps = await env.DB.prepare(`SELECT key,value FROM metadata WHERE key IN
    ('published_at','units_refreshed_at','corpus_counts_at','corpus_documents_at','dates_at') ORDER BY key`).all<{ key: string; value: string }>();
  return stamps.results.map(r => `${r.key}=${r.value}`).join(':');
}
// How many of a character's crops, or its grapheme's, each decade holds by its book's date, the
// collection's and the corpus's apart, with the undated as decade null. Every visitor gets the same
// answer, so the edge keeps one copy per catalogue, corpus count and dates version.
export const localDecadesQuery = (grapheme: boolean, axis: YearOptions['axis']) =>
  `SELECT ${decadeColumn} AS decade,count(*) AS n FROM units u${datingJoin('u.document', axis)} WHERE ${grapheme
    ? 'u.id IN (SELECT id FROM units WHERE origin=? AND family=? UNION SELECT id FROM units WHERE origin=? AND character=?)'
    : 'u.origin=? AND u.character=?'} GROUP BY 1 ORDER BY 1`;
export const corpusDecadesQuery = (grapheme: boolean, axis: YearOptions['axis']) =>
  `SELECT ${decadeColumn} AS decade,count(*) AS n FROM (${corpusSelection(grapheme ? 'family' : 'character', 1)}) x${datingJoin('x.document', axis)} GROUP BY 1 ORDER BY 1`;
async function decades(env: Env, ctx: ExecutionContext, url: URL) {
  const q = url.searchParams, { data } = await known(env, q.get('code_point') || '');
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness', grapheme = q.get('scope') === 'grapheme';
  const family = data.grapheme?.code_point || data.code_point;
  const version = await axisVersion(env);
  const key = new Request(`${url.origin}/layers/decades?v=${encodeURIComponent(version)}&c=${encodeURIComponent(data.code_point)}&s=${grapheme}&a=${axis}`);
  const cached = await caches.default.match(key);
  if (cached) return cached.json();
  const selected = grapheme ? family : data.char;
  const [local, corpus] = await env.DB.batch([
    env.DB.prepare(localDecadesQuery(grapheme, axis)).bind(...(grapheme ? ['local', family, 'local', data.char] : ['local', data.char])),
    env.DB.prepare(corpusDecadesQuery(grapheme, axis)).bind(selected, selected),
  ]);
  const rows = (r: D1Result) => (r.results as { decade: number | null; n: number }[]).map(row => [row.decade, row.n]);
  const body = { code_point: data.code_point, axis, scope: grapheme ? 'grapheme' : 'character', local: rows(local), corpus: rows(corpus) };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': 'public, max-age=3600' } })));
  return body;
}
// A character's or grapheme's crops along its time axis (編年): each decade's crops counted, and a few of
// them drawn by their shuffle so each decade shows a fair sample; the undated as decade null. A crop is
// placed by its book's date on `axis`, and the crops may be narrowed to a style group, a kind of
// production (`handwritten`, `printed`, `inscribed` and what is under it) or some of the grapheme's
// characters (a script's). The answer is the same for every visitor, so the edge keeps it by the
// stamps the decades are kept by (`axisVersion`).
const CHRONOLOGY_MAX = 240;
export type ChronologyFilter = { style: number | null; production: string | null; chars: string[] };
function chronologyFilter(q: URLSearchParams): ChronologyFilter {
  const production = q.get('production');
  if (production && !/^[a-z_]+(\/[a-z_]+)*$/.test(production)) throw new Problem(422, 'Unknown production.');
  const chars = (q.get('chars') ?? '').split(',').filter(Boolean);
  if (chars.length > 20 || chars.some(c => [...c].length > 4)) throw new Problem(422, 'Name at most 20 characters.');
  return { style: styleGroup(q), production, chars };
}
/** The conditions of `filter` on a row whose style order, production and character are these columns. */
export function chronologyConditions(filter: ChronologyFilter, columns: { style: string; production: string; character: string }) {
  const sql: string[] = [], values: (string | number)[] = [];
  if (filter.style !== null) { sql.push(`${columns.style}=?`); values.push(filter.style) }
  // A production and every node under it: `printed` takes `printed/woodblock`, compared as text, not as a LIKE pattern.
  if (filter.production) { sql.push(`(${columns.production}=? OR substr(${columns.production},1,?)=?)`); values.push(filter.production, filter.production.length + 1, filter.production + '/') }
  if (filter.chars.length) { sql.push(`${columns.character} IN (${filter.chars.map(() => '?').join(',')})`); values.push(...filter.chars) }
  return { sql: sql.map(c => ' AND ' + c).join(''), values };
}
// Each decade is ranked on its keys alone, and only the crops kept are read whole: a crop's JSON is
// kilobytes, and sorting it with every crop of a common character would cost more than the answer.
export const localChronologyQuery = (grapheme: boolean, axis: YearOptions['axis'], conditions = '') =>
  `SELECT u.*,k.decade,k.r,k.bucket FROM (SELECT * FROM (SELECT u.rowid AS rid,${decadeColumn} AS decade,
    row_number() OVER (PARTITION BY ${decadeColumn} ORDER BY u.shuffle,u.id) AS r,count(*) OVER (PARTITION BY ${decadeColumn}) AS bucket
    FROM units u${datingJoin('u.document', axis)} WHERE ${grapheme
      ? 'u.id IN (SELECT id FROM units WHERE origin=? AND family=? UNION SELECT id FROM units WHERE origin=? AND character=?)'
      : 'u.origin=? AND u.character=?'}${conditions}) WHERE r<=?) k CROSS JOIN units u ON u.rowid=k.rid ORDER BY k.decade,k.r`;
// A corpus gallery's glyphs by their keys only (`corpusSelection` without the records' JSON): the
// character a decision gave a named glyph, its style group and its document.
export const corpusKeys = (field: 'character' | 'family') => `SELECT c.id AS id,c.document AS document,c.shuffle AS shuffle,
    c.style_order AS s,c.production AS production,CASE WHEN u.id IS NULL THEN c.character ELSE u.character END AS ch
    FROM corpus_units c LEFT JOIN units u ON c.id=u.id WHERE c.${field}=? AND (u.id IS NULL OR u.${field}=c.${field})
  UNION ALL SELECT c.id,c.document,c.shuffle,u.style_order,c.production,u.character
    FROM units u${field === 'character' ? ' INDEXED BY unit_corpus_character_style' : ''} JOIN corpus_units c ON c.id=u.id
    WHERE u.origin='corpus' AND u.${field}=? AND c.${field} IS NOT u.${field}`;
export const corpusChronologyQuery = (grapheme: boolean, axis: YearOptions['axis'], conditions = '') =>
  `SELECT * FROM (SELECT x.id,${decadeColumn} AS decade,row_number() OVER (PARTITION BY ${decadeColumn} ORDER BY x.shuffle,x.id) AS r,
    count(*) OVER (PARTITION BY ${decadeColumn}) AS bucket FROM (${corpusKeys(grapheme ? 'family' : 'character')}) x${datingJoin('x.document', axis)}
    WHERE 1=1${conditions}) WHERE r<=? ORDER BY decade,r`;
// The rows of the corpus glyphs kept, with the row a round or decision gave a named one.
export const corpusRowsQuery = (n: number) => `SELECT c.*,u.data AS overlay,u.written_form AS overlay_form
  FROM corpus_units c LEFT JOIN units u ON u.id=c.id WHERE c.id IN (${Array(n).fill('?').join(',')})`;
async function chronology(env: Env, ctx: ExecutionContext, url: URL) {
  const q = url.searchParams, { data } = await known(env, q.get('code_point') || '');
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness', grapheme = q.get('scope') === 'grapheme';
  const per = Math.max(1, integer(q, 'per', 6, 12)), filter = chronologyFilter(q);
  const family = data.grapheme?.code_point || data.code_point, selected = grapheme ? family : data.char;
  const version = await axisVersion(env);
  const key = new Request(`${url.origin}/layers/chronology?v=${encodeURIComponent(version)}&c=${encodeURIComponent(data.code_point)}&s=${grapheme}&a=${axis}&p=${per}`
    + `&f=${encodeURIComponent(JSON.stringify(filter))}`);
  const cached = await caches.default.match(key);
  if (cached) return cached.json();
  const local = chronologyConditions(filter, { style: 'u.style_order', production: 'u.production', character: 'u.character' });
  const corpus = chronologyConditions(filter, { style: 's', production: 'production', character: 'ch' });
  const [own, glyphs] = await env.DB.batch([
    env.DB.prepare(localChronologyQuery(grapheme, axis, local.sql))
      .bind(...(grapheme ? ['local', family, 'local', data.char] : ['local', data.char]), ...local.values, per),
    env.DB.prepare(corpusChronologyQuery(grapheme, axis, corpus.sql)).bind(selected, selected, ...corpus.values, per),
  ]);
  // At most CHRONOLOGY_MAX crops of each list are drawn: across many decades, each shows fewer.
  const fewer = <T extends { r: number }>(found: T[]) => {
    let k = per;
    while (k > 1 && found.filter(row => row.r <= k).length > CHRONOLOGY_MAX) k--;
    // More decades than the cap: the earliest keep one crop each.
    return found.filter(row => row.r <= k).slice(0, CHRONOLOGY_MAX);
  };
  type Bucket = { decade: number | null; local: number; corpus: number; items: Json[] };
  const buckets = new Map<number | null, Bucket>();
  const bucketOf = (decade: number | null) => buckets.get(decade) ?? buckets.set(decade, { decade, local: 0, corpus: 0, items: [] }).get(decade)!;
  for (const row of own.results as (UnitRow & { decade: number | null; r: number; bucket: number })[]) {
    bucketOf(row.decade).local = row.bucket;
  }
  for (const row of fewer(own.results as (UnitRow & { decade: number | null; r: number; bucket: number })[])) {
    bucketOf(row.decade).items.push({ ...compact(row), origin: 'collection' });
  }
  const found = glyphs.results as { id: string; decade: number | null; r: number; bucket: number }[];
  for (const row of found) bucketOf(row.decade).corpus = row.bucket;
  // A decade shows the collection's crops first; the corpus fills what is left of its `per`, and only
  // those glyphs' records are read.
  const room = (decade: number | null) => per - (buckets.get(decade)?.items.length ?? 0);
  const kept = fewer(found).filter(row => row.r <= room(row.decade));
  const decadeOf = new Map(kept.map(row => [row.id, row.decade]));
  const rows: (CorpusRow & { overlay: string | null; overlay_form: string | null })[] = [];
  for (let i = 0; i < kept.length; i += 90) {
    const ids = kept.slice(i, i + 90).map(row => row.id);
    rows.push(...(await env.DB.prepare(corpusRowsQuery(ids.length)).bind(...ids).all<CorpusRow & { overlay: string | null; overlay_form: string | null }>()).results);
  }
  const order = new Map(kept.map((row, i) => [row.id, i]));
  rows.sort((a, b) => order.get(a.id)! - order.get(b.id)!);
  // Bound simultaneous R2 streams, as a corpus gallery page does.
  for (let i = 0; i < rows.length; i += 8) {
    const records = await Promise.all(rows.slice(i, i + 8).map(async row => ({ row,
      item: { ...(row.overlay ? parse(row.overlay) : await corpusData(env, row)), style: row.style, ...(row.overlay_form ? { written_form: row.overlay_form } : {}) } })));
    for (const { row, item } of records) bucketOf(decadeOf.get(row.id) ?? null).items.push({ ...listing(item), origin: 'corpus' });
  }
  // A decade shows `per` crops, the collection's first, as many as it has, then the corpus's.
  const all = [...buckets.values()];
  for (const bucket of all) bucket.items = bucket.items.slice(0, per);
  const dated = await withDating(env, all.flatMap(b => b.items));
  let at = 0;
  for (const bucket of all) bucket.items = dated.slice(at, at += bucket.items.length);
  const body = { code_point: data.code_point, char: data.char, axis, scope: grapheme ? 'grapheme' : 'character', per,
    buckets: all.filter(b => b.decade !== null).sort((a, b) => a.decade! - b.decade!), undated: buckets.get(null) ?? { decade: null, local: 0, corpus: 0, items: [] } };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': 'public, max-age=3600' } })));
  return body;
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
  // Placed or narrowed by date: each glyph joins its book's date by key (`corpus_units.document`, 0053).
  const dated=widened?null:yearOptions(q,(status,message)=>{throw new Problem(status,message)});
  const join=dated?`FROM (${corpusSelection(field,selected.length)}) x${datingJoin('x.document',dated.axis)}`:`FROM (${corpusSelection(field,selected.length)})`;
  if(dated){const {sql,values:years}=yearCondition(dated.years);if(sql){where.push(sql.slice(5));values.push(...years)}}
  const styled=group===null?where:[...where,'s=?'],styledValues=group===null?values:[...values,group];
  const [count,rows]=await env.DB.batch([
    // A widening counts no further than its cap, and so has no style counts.
    widened?env.DB.prepare(`SELECT count(*) AS n FROM (SELECT 1 ${join} WHERE ${styled.join(' AND ')} LIMIT ${WIDENED_CAP+1})`).bind(...styledValues)
      :env.DB.prepare(`SELECT s,count(*) AS n ${join} WHERE ${where.join(' AND ')} GROUP BY s`).bind(...values),
    env.DB.prepare(`SELECT ${dated?'x.*':'*'} ${join} WHERE ${styled.join(' AND ')} ORDER BY ${dated?.order==='year'?yearOrder('k,s,i'):'k,s,i'} LIMIT ? OFFSET ?`).bind(...styledValues,limit,offset),
  ]);
  const grouped=widened?null:styleCounts(count.results as {s:number;n:number}[],group);
  const counted=grouped?grouped.total:(count.results[0] as {n:number}).n;
  const items=[];
  // Bound simultaneous R2 streams; a corpus page may contain 200 records.
  for(let i=0;i<rows.results.length;i+=8){
    items.push(...await Promise.all((rows.results.slice(i,i+8) as (CorpusRow&{overlay:string|null})[])
      .map(async row=>({...(row.overlay?parse(row.overlay):await corpusData(env,row)),style:row.style}))));
  }
  const formed=await withForms(env,items);
  const info=await known(env,data.char),familyCode=data.grapheme?.code_point||data.code_point;
  const familyCounts=await env.DB.prepare(`SELECT count(*) AS total,sum(written IS NULL) AS unassigned FROM (
    SELECT CASE WHEN u.id IS NULL THEN c.character ELSE u.character END AS written
      FROM corpus_units c LEFT JOIN units u ON c.id=u.id WHERE c.family=? AND (u.id IS NULL OR u.family=c.family)
    UNION ALL SELECT u.character AS written FROM units u JOIN corpus_units c ON c.id=u.id
      WHERE u.origin='corpus' AND u.family=? AND c.family IS NOT u.family
    )`).bind(familyCode,familyCode).first<{total:number;unassigned:number}>();
  return {...data.candidates,code_point:data.code_point,total:widened?Math.min(counted,WIDENED_CAP):counted,capped:widened&&counted>WIDENED_CAP,
    available:formed.length,items:await withDating(env,formed),...(grouped?{styles:grouped.styles}:{}),scope:family?'grapheme':widened?'variants':'character',status:'ok',
    visual_analysis:info.detail.visual_analysis,family_total:familyCounts?.total||0,unassigned_count:familyCounts?.unassigned||0,
    ...(dated?{order:dated.order,axis:dated.axis,years:dated.years}:{})};
}
// A document's characters in source order. The primary key serves the filter and the order, and each
// row joins its unit by id: a published unit gives its current character, state and revision.
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
const formTools: FormTools = {fail:(status,message,extra)=>{throw new Problem(status,message,extra)},body,text,codePoints:cp,
  family:async(env,char)=>(await known(env,char).catch(()=>null))?.data.grapheme?.code_point||cp(char)};
// What the ledger needs of the rest of the Worker (`ledger.ts`): a crop subject is a crop the site
// holds, or a corpus glyph whose row a first claim writes, as a first review would.
const ledgerTools: LedgerTools = {fail:(status,message)=>{throw new Problem(status,message)},body,text,canonical:value=>canonical(value),owned,
  // A corpus glyph nothing has named gets its row first, as its first review would give it one, so the
  // claim names the version SQLite gives the row (`units.crop_version`); a client that saw another
  // version is refused before anything is written.
  crop:async(env,id,expected)=>{let row=await unit(env,id);
    if(row.fresh){
      if(cropVersion(row.id,parse(row.data))!==expected)return {id:row.id,version:cropVersion(row.id,parse(row.data))};
      await materialise(env,row as UnitRow&{fresh:CorpusRow}).run();row=await unit(env,row.id);
    }
    return {id:row.id,version:row.crop_version??null}}};
export function canonical(value: unknown): string {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical((value as Json)[k])).join(',')+'}';
}
function validateAnswer(answer: Json, current: Json, round: boolean, corpus=false, batch=false) {
  if(!Number.isSafeInteger(answer.revision)||answer.revision!==current.revision)throw new Problem(409,'This character changed. Reload it.');
  if(corpus?answer.source_revision!==current.source_revision:answer.image_sha256!==current.image_sha256)throw new Problem(409,'The source image changed. Reload it.');
  if(!['match','wrong','unsure'].includes(answer.verdict))throw new Problem(422,'Choose a review decision.');
  if(answer.issue!=null&&!['character','merged','crop','blank','other','unclear'].includes(answer.issue))throw new Problem(422,'Unknown issue.');
  if(answer.verdict==='match'&&(answer.character||answer.correction||answer.issue))
    throw new Problem(422,'A matching crop cannot also have an issue.');
  if(answer.verdict==='wrong'&&!answer.issue)throw new Problem(422,'Choose an issue.');
  for(const field of ['character','correction'])text(answer[field],32,field);
  // Typed characters describe a joined crop; one character is named as `character`.
  if(answer.correction&&answer.issue!=='merged')throw new Problem(422,'Typed characters belong to a joined-character issue.');
  text(answer.note,2000,'note');
  if(answer.character)writtenCharacter(answer.character);
  if(answer.verdict==='wrong' && answer.character && literal(answer.character)===current.written_character)
    throw new Problem(422,'Choose a different character or a different issue.');
  if(answer.box!==undefined){
    // A redrawn box is the crop's fix: saved as a match on the pixels it names, never in a round.
    if(round||corpus||batch)throw new Problem(422,'This crop cannot be redrawn here.');
    if(answer.verdict!=='match'||answer.issue!=null||answer.character||answer.correction)
      throw new Problem(422,'A redrawn crop is saved as fixed.');
    answer.box=redrawnBox(answer.box,current);
  }
}
async function submit(env: Env, request: Request, actor: string, target?: string) {
  const input=await body(request);
  const corpus=target==='@corpus', batch=target==='@batch';
  if(corpus)target=text(input.identity,512,'corpus identity',true)!;
  if(batch)target=undefined;
  const id=text(input.id,64,'submission id',true)!;
  if(!/^[0-9a-f-]{36}$/i.test(id))throw new Problem(422,'Invalid submission id.');
  // A retry is the same submission whatever else came on screen meanwhile: the seen crops are left
  // out of the signature, as the local server compares only the answers, and the first result stands.
  const {seen:_,skipped:__,...signed}=input;
  const signature=canonical({target:batch?'@batch':target||null,input:signed});
  const key=actor+':'+id;
  // A corpus glyph's inspector shows the record it gets back, with the form its ledger holds now.
  const answered=async(value:Json)=>corpus?(await withForms(env,[value]))[0]:value;
  const previous=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
  if(previous){if(previous.request!==signature)throw new Problem(409,'This submission was already saved with different answers.');return answered(parse(previous.response))}
  const round=!target&&!batch;
  // A round names its grapheme; each crop it answers or saw is one of the grapheme's characters.
  const grapheme=round?graphemeKey(input.grapheme):null;
  if(round&&!grapheme)throw new Problem(422,'A round names its grapheme.');
  const members=grapheme?await graphemeMembers(env,grapheme):null;
  if(batch&&(input.seen!==undefined||input.skipped!==undefined))throw new Problem(422,'Only a round records seen or skipped crops.');
  const correction=batch?validBatch(input):null;
  const {answers,seen,skipped}=batch?{answers:correction!.crops,seen:[],skipped:[]}:validRound(input,target);
  // A redrawn box is held to the rate batch corrections are, by address.
  if(answers.some(answer=>answer.box!==undefined)){
    const {success}=await env.CORRECTIONS.limit({key:request.headers.get('cf-connecting-ip')??'local'});
    if(!success)throw new Problem(429,'Too many corrections at once. Wait a minute and try again.');
  }
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
    validateAnswer(answer,current,round,glyph,batch);
    if(glyph&&(!current.proxyable||(current.identity_status==='unassigned'&&answer.verdict==='match')))
      throw new Problem(422,'Choose a written character or report an issue.');
    if(round&&(!row.quiz||!members!.includes(current.label)))throw new Problem(409,'This round changed. Reload it.');
    const written=answer.character?literal(answer.character):null;
    // A batch names the character only: a crop reported for its box, a blank or a merge stays reported.
    const kept=batch&&current.state==='flagged'&&current.issue&&current.issue!=='character'?current.issue:null;
    const resolved=!kept&&(answer.verdict==='match'||Boolean(answer.issue==='character'&&written));
    const family=written?(await lookup(written))?.data.grapheme?.code_point:null;
    const next:Json={...current,revision:current.revision+1,state:resolved?'checked':'flagged',
      ...(written?{label:written,char:written,code_point:cp(written),written_character:written,identity_status:'assigned',identity_basis:'human_review',script:/\p{Script=Katakana}/u.test(written)?'katakana':/\p{Script=Hiragana}/u.test(written)?'hiragana':/\p{Script=Han}/u.test(written)?'han':/\p{Script=Hangul}/u.test(written)?'hangul':isGugyeol(written)?'gugyeol':'symbol'}:{}),
      ...(written?{grapheme:family||cp(written),visual_group:null,category:categoryOf(written)}:{}),
      issue:resolved?null:kept??answer.issue,
      // A redrawn box is the crop's until the next publication cuts it; the image shown is still the old cut.
      ...(answer.box?{box:answer.box,box_pending:true}:{})};
    const snapshot={...parse(row.snapshot),character:compact(row)};
    // A round or batch keeps its request once, on the submission; each event names it and carries only its
    // own answer, so a submission of many crops stays well inside D1's row size. A single crop's review
    // keeps its request whole: it is that one answer.
    const evidence={kind:round?'visual-quiz':'character-review',...(round?{round:id,grapheme,label:current.label}:{}),...(batch?{batch:id}:round?{answer}:{request:input}),
      verdict:answer.verdict,issue:answer.issue||null,note:answer.note||'',
      suggested_character:written?cp(written):null,suggested_text:answer.correction||null,snapshot,
      correction:{unicode:cp(next.label),box:next.box},
      // The box claim and the evidence it was made on: the pixels and the box the crop was cut with.
      ...(answer.box?{recrop:{from:current.box??null,to:answer.box,pixels:current.image_sha256}}:{})};
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
      if(same&&members!.includes(data.label)&&(crop.image===undefined||crop.image===data.image)){
        list.push(crop);
        if(row.fresh)fresh.push(row as UnitRow&{fresh:CorpusRow});
      }
    });
  }
  const result=corpus?{...(changes[0].next),origin:'corpus',event:changes[0].event}
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
    if(repeat?.request===signature)return answered(parse(repeat.response));
    if(String(error).includes('review_revision_conflict'))throw new Problem(409,'Another review changed this crop. Reload it.');
    throw error;
  }
  return answered(result);
}
// A submission is keyed by the id it was written under, so a user's own is found under any of theirs.
async function undo(env:Env,request:Request,actor:string,id:string){
  await body(request);
  const submission=await env.DB.prepare("SELECT * FROM submissions WHERE id IN (SELECT actor||':'||? FROM actors WHERE user_id=?)").bind(id,actor).first<Json>();
  if(!submission)throw new Problem(404,'No saved round belongs to this reviewer.');
  return {id,...await revert(env,submission,actor)};
}
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
// Reject an admin's selection: the named submissions, or all of one reviewer's a page at a time.
// They go newest first, so a crop two of them touched goes back step by step. One that a later
// review by someone else has built on is left as it is and reported; `next` continues after the page.
const REJECT_PAGE=40;
// Each crop a rejection puts back costs a few queries, and one request may make a thousand: a page
// stops before the crops it has put back pass this many.
const REJECT_CROPS=250;
async function reject(env:Env,admin:string,input:Json){
  const reason=text(input.reason??'',500,'reason')??'';
  const crops='(SELECT count(*) FROM events WHERE submission=submissions.id AND kind=\'review\') AS crops';
  let rows:Json[],reviewer:[string,string[]]|undefined;
  if(Array.isArray(input.submissions)){
    const keys=input.submissions.slice(0,REJECT_PAGE).map((key:unknown)=>text(key,256,'submission',true)!);
    if(!keys.length)throw new Problem(422,'Name the submissions to reject.');
    rows=(await env.DB.prepare(`SELECT *,${crops} FROM submissions WHERE id IN (${keys.map(()=>'?').join(',')}) AND undone=0 ORDER BY at DESC,id DESC`).bind(...keys).all<Json>()).results;
  }else{
    reviewer=reviewerActors(input);
    const [actors,values]=reviewer,before=input.next?decodeCursor(text(input.next,512,'cursor',true)!):{at:'9999',id:''};
    rows=(await env.DB.prepare(`SELECT *,${crops} FROM submissions WHERE undone=0 AND actor IN ${actors} AND (at,id)<(?,?) ORDER BY at DESC,id DESC LIMIT ?`)
      .bind(...values,before.at,before.id,REJECT_PAGE).all<Json>()).results;
  }
  let rejected=0,spent=0;const conflicts:string[]=[],unavailable:string[]=[],done:Json[]=[];
  for(const submission of rows){
    if(spent&&spent+submission.crops>REJECT_CROPS)break;
    spent+=submission.crops;done.push(submission);
    try{await revert(env,submission,admin,{reason,reviewer});rejected++}
    catch(error){
      // A crop someone else has changed since stays as it is; a crop no longer in the collection
      // cannot be put back. Either way the rest go on.
      if(error instanceof Problem&&error.status===409)conflicts.push(submission.id);
      else if(error instanceof Problem&&error.status===404)unavailable.push(submission.id);
      else throw error;
    }
  }
  const last=done.at(-1),more=done.length<rows.length||(!Array.isArray(input.submissions)&&rows.length===REJECT_PAGE);
  return {rejected,conflicts,unavailable,
    next:!Array.isArray(input.submissions)&&more&&last?encodeCursor(last.at,last.id):null,
    left:Array.isArray(input.submissions)?rows.slice(done.length).map(row=>row.id):[]};
}
// The journal ids of one reviewer: a user's, or one id from before accounts that nobody holds.
export function reviewerActors(input:Json):[string,string[]]{
  if(typeof input.user==='string')return ['(SELECT actor FROM actors WHERE user_id=?)',[text(input.user,64,'user',true)!]];
  if(typeof input.actor==='string')return ['(?)',[text(input.actor,128,'actor',true)!]];
  throw new Problem(422,'Name a user or a reviewer id.');
}
// A review's label sits in evidence.label (a round) or evidence.snapshot.character.label (a single
// correction); evidence is itself a JSON string, parsed once. An undo's own evidence is the plain string
// 'undo of <event id>', not JSON, so its label comes from the row's own snapshot column instead — the
// CASE only evaluates the branch for the row's own kind, so a future kind touches neither column.
// This text is repeated verbatim in migration 0011's expression index; keep the two in sync.
export const historyLabelExpr = () => `(CASE kind WHEN 'review' THEN coalesce(json_extract(json_extract(event,'$.evidence'),'$.label'),json_extract(json_extract(event,'$.evidence'),'$.snapshot.character.label')) WHEN 'undo' THEN json_extract(snapshot,'$.character.label') END)`;
// Newest first, keyset-paged on (at,id): `before` is strictly older than that pair, in index order.
// Given `actors`, the rows are those written under any of those ids: one arm per id, each read in
// event_actor_history order, merged by the compound ORDER BY so the read stops at the LIMIT instead of
// sorting everything the ids ever wrote. Each row names the user who holds its id now, or none for a
// reviewer id from before accounts that nobody has claimed. Parameters are numbered so the label and
// cursor are bound once for every arm; the LIMIT is the parameter after `values`.
export function historyQuery(actors: string[] | null, label: string | null, cursor: { at: string; id: string } | null): { sql: string; values: (string | number)[] } {
  const values: (string | number)[] = [];
  const bind = (value: string) => `?${values.push(value)}`;
  const where = [`kind IN ('review','undo')`];
  if (label !== null) where.push(`${historyLabelExpr()}=${bind(label)}`);
  if (cursor) where.push(`(at,id)<(${bind(cursor.at)},${bind(cursor.id)})`);
  const select = (filter: string[]) => `SELECT id,at,actor,target,kind,event,${historyLabelExpr()} AS label FROM events WHERE ${filter.join(' AND ')}`;
  // A standing round is listed once more for the crops it passed: a pass is not a review of the crop,
  // so it is its own row, with how many crops it passed and the round's character.
  const passedWhere = ['undone=0', `EXISTS(SELECT 1 FROM seen k WHERE k.submission=submissions.id)`];
  if (label !== null) passedWhere.push(`json_extract(request,'$.input.label')=${bind(label)}`);
  if (cursor) passedWhere.push(`(at,id)<(${bind(cursor.at)},${bind(cursor.id)})`);
  const passed = (filter: string[]) => `SELECT id,at,actor,NULL AS target,'passed' AS kind,
    (SELECT count(*) FROM seen k WHERE k.submission=submissions.id) AS event,json_extract(request,'$.input.label') AS label
    FROM submissions WHERE ${filter.join(' AND ')}`;
  const arms = actors === null ? [select(where), passed(passedWhere)]
    : actors.flatMap(actor => [select([...where, `actor=${bind(actor)}`]), passed([...passedWhere, `actor=${bind(actor)}`])]);
  const sql = `SELECT h.*,a.user_id AS user,u.name AS name,u.image AS image FROM (${arms.join(' UNION ALL ')}
    ORDER BY at DESC,id DESC LIMIT ?${values.length + 1}) h
    LEFT JOIN actors a ON a.actor=h.actor LEFT JOIN "user" u ON u.id=a.user_id ORDER BY h.at DESC,h.id DESC`;
  return { sql, values };
}
// D1 binds at most 100 parameters to a statement: a user holding more ids than one statement takes is
// read in groups, each bounded by the LIMIT, and the groups' pages merged here.
const HISTORY_ARMS = 64;
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
// A review keeps the words it was saved with. One saved while crops carried a reading names the
// wrong-character issue `reading` and its typed characters `suggested_reading`.
export const recordedIssue = (issue: unknown) => issue === 'reading' ? 'character' : issue ?? null;
export const typedText = (evidence: Json | null | undefined): string | null =>
  evidence?.suggested_text ?? evidence?.suggested_reading ?? null;
type HistoryRow = { id: string; at: string; actor: string; target: string; kind: string; event: string; label: string | null;
  user: string | null; name: string | null; image: string | null };
// A review's evidence names its own verdict, issue and correction; an undo's evidence is only the id
// of the event it reverses, so those fields stay null and `undoes` names that event instead.
export function historyItem(row: HistoryRow, me: string | null = null): Json {
  const reviewer = { user: row.user, name: row.name ?? row.actor, image: row.image, mine: Boolean(me && row.user === me) };
  if (row.kind === 'passed') return { id: row.id, at: row.at, target: null, label: row.label, kind: 'passed', reviewer,
    passed: Number(row.event), verdict: null, issue: null, character: null, text: null, round: row.id, batch: null, undoes: null };
  const undo = row.kind === 'undo';
  const parsedEvent = parse(row.event);
  const evidence = undo ? null : parse(parsedEvent.evidence);
  return {
    id: row.id, at: row.at, target: row.target, label: row.label, kind: row.kind as 'review' | 'undo',
    reviewer,
    verdict: evidence?.verdict ?? null,
    issue: recordedIssue(evidence?.issue),
    character: evidence?.suggested_character ? literal(evidence.suggested_character) : null,
    text: typedText(evidence),
    round: evidence?.round ?? null,
    batch: evidence?.batch ?? null,
    undoes: undo ? String(parsedEvent.evidence).replace(/^undo of /, '') : null,
  };
}
// Each decision with the dates of the book its crop comes from: a local crop's row names the book, a
// named corpus glyph's record its source.
export const historyDocumentsQuery = (n: number) => `SELECT id,coalesce(document,json_extract(data,'$.source.document_id')) AS document
  FROM units WHERE id IN (${Array(n).fill('?').join(',')})`;
async function historyDating(env: Env, items: Json[]): Promise<Json[]> {
  const targets = [...new Set(items.map(item => item.target).filter(Boolean))] as string[];
  if (!targets.length) return items;
  const documents = new Map<string, string>();
  for (const part of chunks(targets, 90))
    for (const row of (await env.DB.prepare(historyDocumentsQuery(part.length)).bind(...part).all<{ id: string; document: string | null }>()).results)
      if (row.document) documents.set(row.id, row.document);
  return withDating(env, items, item => documents.get(item.target) ?? null);
}
export async function history(env: Env, q: URLSearchParams, me: string | null) {
  const limit = Math.max(1, integer(q, 'limit', 40, 100));
  if (q.get('mine') === 'true' && !me) return { items: [], next: null };
  const user = q.get('mine') === 'true' ? me : q.get('user') ? text(q.get('user'), 64, 'user', true) : null;
  const label = q.get('label') ? text(q.get('label'), 32, 'label', true) : null;
  const before = q.get('before') ? decodeCursor(q.get('before')!) : null;
  const actors = user ? (await env.DB.prepare('SELECT actor FROM actors WHERE user_id=? ORDER BY actor').bind(user).all<{ actor: string }>()).results.map(r => r.actor) : null;
  const groups = actors === null ? [null] : Array.from({ length: Math.ceil(actors.length / HISTORY_ARMS) }, (_, i) => actors.slice(i * HISTORY_ARMS, (i + 1) * HISTORY_ARMS));
  const pages = await Promise.all(groups.map(group => {
    const { sql, values } = historyQuery(group, label, before);
    return env.DB.prepare(sql).bind(...values, limit + 1).all<HistoryRow>();
  }));
  const rows = pages.flatMap(page => page.results);
  if (pages.length > 1) rows.sort((a, b) => a.at === b.at ? (a.id < b.id ? 1 : a.id > b.id ? -1 : 0) : a.at < b.at ? 1 : -1);
  const items = await historyDating(env, rows.slice(0, limit).map(row => historyItem(row, me)));
  // The cursor is the last row returned; the next page starts strictly after it.
  const next = rows.length > limit ? encodeCursor(rows[limit - 1].at, rows[limit - 1].id) : null;
  return { items, next };
}
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
/** Whether a suggestions request names the pixels the unit holds now: a corpus glyph by its source
 *  revision, a crop by its page hash, and a crop published without a hash by its image. */
export function samePixels(q: URLSearchParams, origin: string, data: Json): boolean {
  if (origin === 'corpus') return q.get('source_revision') === data.source_revision;
  return data.image_sha256 ? q.get('image_sha256') === data.image_sha256 : q.get('image') === data.image;
}

const routes = {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
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
        if(path==='/api/admin/reject')return json(await reject(env,me.id,await body(request)));
        if(path==='/api/account/avatar'){
          const origin=request.headers.get('origin');
          if(origin&&origin!==url.origin)throw new Problem(403,'Use the account page on this site.');
          const {status,body:out}=await setAvatar(env,me,q.get('source')??'',request);return json(out,status);
        }
        if(path==='/api/account/claim'){const {status,body:out}=await claim(env,me,String((await body(request)).reviewer??''));return json(out,status)}
        if(path==='/atlas/corpus/reviews')return json(await submit(env,request,me.id,'@corpus'));
        if(path==='/atlas/rounds')return json(await submit(env,request,me.id));
        if(path==='/atlas/corrections'){
          // A batch changes many crops at once, so each address is held to a rate.
          const {success}=await env.CORRECTIONS.limit({key:request.headers.get('cf-connecting-ip')??'local'});
          if(!success)throw new Problem(429,'Too many corrections at once. Wait a minute and try again.');
          return json(await submit(env,request,me.id,'@batch'));
        }
        const acted=path.match(/^\/atlas\/claims\/([^/]+)\/actions$/),picked=path.match(/^\/atlas\/characters\/([^/]+)\/form$/);
        if(path==='/atlas/claims'||acted||picked){
          // A claim, an action or a crop's form is a few small rows, and one address is held to a rate.
          const {success}=await env.CLAIMS.limit({key:request.headers.get('cf-connecting-ip')??'local'});
          if(!success)throw new Problem(429,'Too many claims at once. Wait a minute and try again.');
          const input=await body(request);
          let target='';
          try{target=decodeURIComponent((acted??picked)?.[1]??'')}catch{throw new Problem(404,acted?'No such claim.':'This character is not in the published collection.')}
          if(picked)return json(await setForm(env,input,target,me.id,{...ledgerTools,literal}));
          return json(acted?await actOnClaim(env,input,target,me.id,me.admin,ledgerTools):await writeClaim(env,input,me.id,ledgerTools));
        }
        const undone=path.match(/^\/atlas\/(?:rounds|corrections)\/([^/]+)\/undo$/);
        if(undone)return json(await undo(env,request,me.id,decodeURIComponent(undone[1])));
        const edit=path.match(/^\/(?:atlas\/characters|layers\/units)\/([^/]+)$/);
        if(edit)return json(await submit(env,request,me.id,decodeURIComponent(edit[1])));
        const formed=await formsRoute(env,request,path,q,formTools,ctx,me.id);
        if(formed)return formed instanceof Response?formed:json(formed);
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path.startsWith('/api/admin/')){
        if(!(await viewer(env,request,true))?.admin)throw new Problem(403,'Only an admin can see this.');
        if(path==='/api/admin/reviewers')return json(await reviewers(env,q));
        if(path==='/api/admin/submissions')return json(await submissions(env,q,reviewerActors(Object.fromEntries(q))));
        throw new Problem(404,'Unknown endpoint.');
      }
      const picture=path.match(AVATAR_PATH);
      if(picture)return await avatar(env,picture[1],picture[2]);
      if(path==='/api/account/connections'){const me=await viewer(env,request,true);if(!me)throw new Problem(401,'Sign in to see your account.');return json(await connections(env,me.id))}
      if(path==='/api/account')return json({user:await viewer(env,request,true),providers:providers(env)});
      if(path==='/health')return json({ok:true,published_at:await meta(env,'published_at')});
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image)return await media(env,request,image[1],ctx);
      // A round leaves out what its reviewer skipped lately, so it reads who is asking.
      if(path==='/atlas')return json(await catalogue(env,ctx,url,q.get('purpose')==='review'?(await viewer(env,request))?.id??null:null));
      if(path==='/api/ranking')return await ranking(env,url,ctx);
      if(path==='/atlas/history')return json(await history(env,q,(await viewer(env,request))?.id??null));
      const run=path.match(/^\/atlas\/ngrams\/(\d+)(?:\/([^/]+))?$/);
      if(run)return json(run[2]===undefined?await ngrams(env,ctx,url,ngramSize(run[1])):await ngramOccurrences(env,url,ngramSize(run[1]),decodeURIComponent(run[2])));
      if(path==='/atlas/corpus/characters')return json(await corpusCharacters(env,ctx,url),200,{'cache-control':'private, max-age=300'});
      if(path==='/atlas/corpus/character')return json(await inspected(env,await unit(env,q.get('id')||'')));
      if(path==='/atlas/collection/status')return json(await meta(env,'collection'));
      if(path==='/atlas/dates/stats')return json(await dateStats(env,ctx,url));
      const document=path.match(/^\/atlas\/documents\/([^/]+)\/characters$/);
      if(document){let id:string;try{id=decodeURIComponent(document[1])}catch{throw new Problem(404,'No characters are published for this document.')}
        return await documentCharacters(env,url,id,ctx)}
      const visualSample=path.match(/^\/layers\/visual-groups\/samples\/([^/]+)\/image$/);
      if(visualSample){const data=parse((await unit(env,decodeURIComponent(visualSample[1]))).data);
        if(!data.image)throw new Problem(404,'Image not found.');
        return Response.redirect(new URL(data.image,url).href,302)}
      if(path==='/atlas/claims')return json(await claimsOf(env,text(q.get('subject'),512,'subject',true)!));
      if(path==='/atlas/ledger'||path==='/atlas/ledger.json')return json(await ledgerPage(env,q,integer),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-ledger.json"'}:{});
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviews(env,q.get('include_processed')==='true'),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const versions=path.match(/^\/atlas\/characters\/([^/]+)\/versions$/);
      if(versions){let id:string;try{id=decodeURIComponent(versions[1])}catch{throw new Problem(404,'This character is not in the published collection.')}
        return json(await cropVersions(env,id))}
      if(path==='/atlas/similar/model')return json(await modelInfo(env));
      if(path.startsWith('/atlas/similar/files/')){
        const file=await modelFile(env,path.slice('/atlas/similar/files/'.length),request,ctx);
        if(!file)throw new Problem(404,'No such model file.');
        return file;
      }
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
        return json(await inspected(env,row));
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
      if(path==='/layers/decades')return json(await decades(env,ctx,url));
      if(path==='/layers/chronology')return json(await chronology(env,ctx,url));
      if(path==='/layers/summary')return json(await meta(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures'){
        const selector=path.endsWith('ligatures')?"json_extract(data,'$.ligature') IS NOT NULL":"json_array_length(json_extract(data,'$.grapheme.members'))>1";
        const rows=await env.DB.prepare(`SELECT data FROM characters WHERE ${selector} LIMIT ? OFFSET ?`).bind(integer(q,'limit',200,500),integer(q,'offset',0)).all<{data:string}>();
        return json({items:rows.results.map(r=>parse(r.data)),total:rows.results.length})}
      if(path==='/atlas/corpus/reviews'){
        const rows=await env.DB.prepare("SELECT * FROM units WHERE origin='corpus' AND state='flagged' ORDER BY id LIMIT 96").all<UnitRow>();
        return json({items:await withDating(env,await withForms(env,rows.results.map(compact))),total:rows.results.length})}
      if(path.startsWith('/atlas/forms/')){const formed=await formsRoute(env,request,path,q,formTools,ctx);
        if(formed)return formed instanceof Response?formed:json(formed)}
      throw new Problem(404,'Unknown endpoint.');
    }catch(error){
      const open=path.startsWith('/atlas/documents/')?OPEN:{};
      if(error instanceof Problem)return json({detail:error.message,...error.extra},error.status,
        error.extra.code==='busy'?{...open,'retry-after':String(RETRY_AFTER)}:open);
      throw error;
    }
  },
};

export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    // A read is tried again while D1 is busy; a write is answered at once, and the browser keeps it
    // until the database is back.
    const read=request.method==='GET'||request.method==='HEAD';
    const outcome=await retried(()=>routes.fetch(request,env,ctx),read?READ_BUDGET:0);
    if('value' in outcome)return outcome.value;
    const path=new URL(request.url).pathname,open=path.startsWith('/atlas/documents/')?OPEN:{};
    const busy=transient(outcome.error);
    console.error(JSON.stringify({event:'request_failed',path,method:request.method,busy,attempts:outcome.attempts,...described(outcome.error)}));
    if(busy)return json({detail:'The database is updating. Try again in a moment.',code:'busy'},503,{...open,'retry-after':String(RETRY_AFTER)});
    return json({detail:'The request could not be completed.',code:'failed'},500,open);
  },
} satisfies ExportedHandler<Env>;
