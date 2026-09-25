// Catalogue snapshots are published offline. All online review mutations use D1 transactions.
type Json = Record<string, any>;
type UnitRow = { id: string; origin: string; character: string | null; state: string; revision: number;
  quiz: number; category?: string; data: string; snapshot: string; context: string; visual: string;
  // A corpus glyph nothing has named yet: it has no `units` row, and this is where it is published.
  fresh?: CorpusRow };
type CorpusRow = {id:string;character:string|null;family:string|null;visual_group:string|null;production:string;shuffle:number;object:string;offset:number;size:number};
class Problem extends Error {
  constructor(public status: number, message: string) { super(message) }
}
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const parse = (value: string): Json => JSON.parse(value);
const unavailable = { status: 'unavailable', candidates: [] };
// A label's category is its first character's script, as the migrations and publication scripts compute it.
export const categoryOf=(value:string)=>{const first=[...value][0]??'';return /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(first)?'kana':/\p{Script=Han}/u.test(first)?'kanji':/\p{Script=Hangul}/u.test(first)?'hangul':'other'};
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
async function unit(env: Env, id: string): Promise<UnitRow> {
  const row = await env.DB.prepare('SELECT * FROM units WHERE id=?').bind(id).first<UnitRow>();
  if(row)return row;
  const pointer=await env.DB.prepare('SELECT * FROM corpus_units WHERE id=?').bind(id).first<CorpusRow>();
  if(!pointer)throw new Problem(404, 'This character is not in the published collection.');
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
  return env.DB.prepare('INSERT OR IGNORE INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
    .bind(row.id,'corpus',d.written_character||null,d.reading||null,d.grapheme||null,d.visual_group?.id||null,row.fresh.production,
      row.category||categoryOf(d.label),d.state,d.revision,row.quiz,1,row.fresh.shuffle,row.data,row.snapshot,row.context,row.visual);
}
async function corpusData(env:Env,row:CorpusRow):Promise<Json>{
  if(row.size>128*1024)throw new Problem(503,'Invalid published record.');
  const object=await env.MEDIA.get(row.object,{range:{offset:row.offset,length:row.size}});
  if(!object)throw new Problem(503,'The corpus publication is incomplete.');
  return object.json<Json>();
}
function compact(row: UnitRow): Json {
  return listing(parse(row.data));
}
// A record as a listing shows it, without the fields only its inspector needs.
function listing(d: Json): Json {
  const { text, line, context_image, context_box, crop_box, ...rest } = d;
  return rest;
}
// A pending crop that a round showed and left unflagged, and that still has the box it was seen with,
// is `seen`: out of the queue, and no decision. An undone round's rows stop counting.
// Skips that still count: from a round that was not undone, at the crop's current box.
const SKIPS = `FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
  WHERE k.target=units.id AND k.box IS json_extract(units.data,'$.box')`;
// A pending crop two reviewers skipped is `hard`: it leaves the rounds for its own list.
const HARD = `(SELECT count(DISTINCT k.actor) ${SKIPS})>=2`;
// A standing review from the character inspector (`character-review`), not a round's own verdict;
// an undone review stops counting.
export const reviewedInInspectorQuery = () => `EXISTS(SELECT 1 FROM events e JOIN submissions f ON f.id=e.submission AND f.undone=0
  WHERE e.target=units.id AND e.kind='review' AND json_extract(json_extract(e.event,'$.evidence'),'$.kind')='character-review')`;
const REVIEWED_IN_INSPECTOR = reviewedInInspectorQuery();
const SEEN = `EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
  WHERE s.target=units.id AND s.box IS json_extract(units.data,'$.box'))`;
const EFFECTIVE_STATE = `iif(state='pending' AND ${HARD},'hard',iif(state='pending' AND ${SEEN},'seen',state))`;
// How long a crop a reviewer skipped stays out of that reviewer's own rounds.
const SKIP_REST_MS = 3 * 24 * 60 * 60 * 1000;
const quoted = (value: string) => `'${value.replaceAll("'", "''")}'`;
// The state as one reviewer sees it: a crop they skipped lately is `skipped` for them.
function stateFor(reviewer: string | null): string {
  if (!reviewer) return EFFECTIVE_STATE;
  const since = new Date(Date.now() - SKIP_REST_MS).toISOString();
  // The reviewer's own recent skip is tested first: it is one indexed probe and false for most rows,
  // so the full state is evaluated once per row.
  return `iif(state='pending' AND EXISTS(SELECT 1 ${SKIPS} AND k.actor=${quoted(reviewer)} AND k.at>${quoted(since)})
    AND NOT ${HARD} AND NOT ${SEEN},'skipped',${EFFECTIVE_STATE})`;
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
  if (!Array.isArray(answers) || !Array.isArray(seen) || !Array.isArray(skipped)) throw new Problem(422, 'A round needs 1–96 distinct crops.');
  const ids = [...answers, ...seen, ...skipped].map(crop => crop?.id);
  if (ids.length < 1 || ids.length > 96 || new Set(ids).size !== ids.length) throw new Problem(422, 'A round needs 1–96 distinct crops.');
  for (const crop of [...seen, ...skipped]) {
    text(crop?.id, 512, 'character id', true);
    if (typeof pixels(crop) !== 'string' || !/^[a-f0-9]{64}$/.test(pixels(crop))) throw new Problem(422, 'Invalid image hash.');
    if (crop.image !== undefined) text(crop.image, 256, 'crop image', true);
  }
  return { answers, seen, skipped };
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
async function catalogue(env: Env, q: URLSearchParams) {
  const purpose = q.get('purpose') || 'browse';
  const production = q.get('production') || (purpose === 'review' ? REVIEW_SCOPE : 'all');
  if (!validScope(production)) throw new Problem(400, 'Invalid production scope.');
  const review = purpose === 'review';
  const seed = integer(q, 'seed', 0, 2147483647);
  const limit = integer(q, 'limit', 60, 96), offset = integer(q, 'offset', 0);
  if (review && offset > ROUND_OFFSET_MAX) throw new Problem(404, 'A round does not page this far.');
  const where = ["origin='local'"];
  const values: (string | number)[] = [];
  if (review) where.push('quiz=1');
  const [materials, materialValues] = material(production, 'production');
  where.push(materials); values.push(...materialValues);
  const reviewer = q.get('reviewer') ? text(q.get('reviewer'), 128, 'reviewer', true)! : null;
  const state = stateFor(reviewer);
  const [groups, published] = await env.DB.batch([
    env.DB.prepare(`SELECT character AS label,${state} AS state,count(*) AS n FROM units WHERE ${where.join(' AND ')} GROUP BY 1,2`).bind(...values),
    ...(review ? [corpusCountQuery(production)].map(({ sql, values }) => env.DB.prepare(sql).bind(...values)) : []),
  ]) as D1Result<{label:string;state?:string;n:number}>[];
  const categories = new Map<string, Json>();
  const counts: Json = { pending: 0, seen: 0, flagged: 0, checked: 0, hard: 0, skipped: 0 };
  const add = (label: string, state: string, n: number) => {
    const category = categories.get(label) || { label, total: 0, pending: 0, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 };
    category.total += n; category[state] += n; counts[state] += n;
    categories.set(label, category);
  };
  for (const row of groups.results) add(row.label, row.state!, row.n);
  // A corpus glyph nothing has named is pending for everyone, and the table counts them per character.
  const corpus = new Map<string, number>();
  if (review) for (const row of published.results) if (row.n > 0) { corpus.set(row.label, row.n); add(row.label, 'pending', row.n) }
  const reading = q.get('reading');
  if (reading) { where.push('character=?'); values.push(reading) }
  if (q.get('q')) { where.push('(character=? OR reading=?)'); values.push(literal(q.get('q')!), literal(q.get('q')!)) }
  if (q.get('group') && q.get('group') !== 'all') { where.push('category=?'); values.push(q.get('group')!) }
  // `attention` is the Flagged view: every crop waiting for a person, flagged or hard to read.
  if (q.get('state') === 'attention') where.push(`${state} IN ('flagged','hard')`);
  else if (q.get('state') && q.get('state') !== 'all') { where.push(`${state}=?`); values.push(q.get('state')!) }
  // The Flagged view hides crops already looked at in the inspector by default; `reported=show`
  // (the default for every other caller) leaves them in. `reportedCountWhere` is captured before the
  // hide filter, so the count is of what is hidden, not what remains.
  const flaggedView = ['flagged', 'attention'].includes(q.get('state') ?? '');
  const reportedCountWhere = flaggedView ? [...where, REVIEWED_IN_INSPECTOR] : null;
  if (flaggedView && q.get('reported') === 'hide') where.push(`NOT ${REVIEWED_IN_INSPECTOR}`);
  // A round of one character deals its named and then its untouched corpus glyphs after its local crops.
  const dealt = review && reading !== null && ['all', 'pending'].includes(q.get('state') || 'all') && !q.get('q')
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
  const [count, window, reportedCount] = await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n FROM ${from}`).bind(...fromValues),
    env.DB.prepare(`SELECT *,${state} AS effective,(SELECT shape_order FROM unit_shapes s WHERE s.id=units.id) AS shape_order FROM ${from} ORDER BY ${order}${reviewedLast} ((shuffle * ?) % 2147483647),id LIMIT ? OFFSET ?`).bind(...fromValues, seed + 1, limit, offset),
    ...(reportedCountWhere ? [env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${reportedCountWhere.join(' AND ')}`).bind(...values)] : []),
  ]);
  const listed = (count.results[0] as { n: number }).n;
  const items: Json[] = (window.results as (UnitRow & { effective: string; shape_order: number | null })[])
    .map(row => ({ ...compact(row), state: row.effective, shape_order: row.shape_order }));
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
    counts, purpose, production, review_limit:96, review_epoch: await meta(env, 'review_epoch') || 0, query: q.get('q'),
    categories: [...categories.values()].sort((a,b) => b.total-a.total || a.label.localeCompare(b.label)),
    reported_count: reportedCount ? (reportedCount.results[0] as { n: number }).n : 0,
    items };
}
// Untouched assigned corpus glyphs per character in this material.
export function corpusCountQuery(production: string) {
  const [materials, values] = material(production, 'production');
  return { sql: `SELECT character AS label,sum(n-named) AS n FROM corpus_characters WHERE ${materials} GROUP BY character`, values };
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
  // `shuffle` is the first 28 bits of the id's SHA-256.
  const start = seed % 268435456, wanted = offset + limit;
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
        items.push({ ...listing(data), origin: 'corpus', state: 'pending', shape_order: null });
    }
  }
  return { items, read: read.length, exhausted: rows.length < wanted };
}
function chunks<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
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
  return { query: term, items: rows.results.slice(0,limit).map(r => ({...parse(r.data), rank:r.rank})),
    total: rows.results.length, more: Math.max(0,rows.results.length-limit), status:'ok',corpus:{ready:true} };
}
async function occurrences(env: Env, code: string, q: URLSearchParams, origin = 'local') {
  const { data } = await known(env, code);
  if(origin==='corpus')return corpusOccurrences(env,data,q);
  const limit = integer(q,'limit',24,200), offset=integer(q,'offset',0);
  const values: (string | number)[] = [origin];
  const where = ['origin=?'];
  if (q.get('scope') === 'grapheme' || q.get('expand') === 'grapheme') {
    const family = data.grapheme?.code_point || data.code_point;
    where.push('(family=? OR character=?)'); values.push(family,data.char);
  } else { where.push('character=?'); values.push(data.char) }
  if (q.get('visual_group')) {
    if(q.get('visual_group')==='unassigned') where.push('visual_group IS NULL');
    else { where.push('visual_group=?'); values.push(q.get('visual_group')!) }
  }
  if (q.get('state') && q.get('state')!=='all') { where.push('state=?'); values.push(q.get('state')!) }
  const [count, rows] = await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n FROM units WHERE ${where.join(' AND ')}`).bind(...values),
    env.DB.prepare(`SELECT * FROM units WHERE ${where.join(' AND ')} ORDER BY id LIMIT ? OFFSET ?`).bind(...values,limit,offset),
  ]);
  const total = (count.results[0] as {n:number}).n;
  return { ...data.candidates, query: data.code_point, code_point: data.code_point,
    total, available: rows.results.length, items:(rows.results as UnitRow[]).map(compact),
    counts:{ total, exact:total, exact_total:total }, scope:q.get('scope') || 'character', status:'ok' };
}
async function corpusOccurrences(env:Env,data:Json,q:URLSearchParams){
  const limit=integer(q,'limit',24,200),offset=integer(q,'offset',0);
  const family=q.get('scope')==='grapheme',field=family?'family':'character';
  const selected=family?(data.grapheme?.code_point||data.code_point):data.char;
  // Two indexed branches include corrections into this category and remove corrections
  // out of it. Unassigned source classes remain null until evidence identifies the form.
  const selection=`SELECT c.*,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
    FROM corpus_units c LEFT JOIN units u ON c.id=u.id
    WHERE c.${field}=? AND (u.id IS NULL OR u.${field}=c.${field})
    UNION ALL SELECT c.*,u.data AS overlay,u.visual_group AS overlay_group,u.character AS overlay_character
    FROM units u JOIN corpus_units c ON c.id=u.id
    WHERE u.origin='corpus' AND u.${field}=? AND c.${field} IS NOT u.${field}`;
  const where=['1=1'],values:(string|number)[]=[selected,selected];
  if(q.get('visual_group')){if(q.get('visual_group')==='unassigned')where.push('(CASE WHEN overlay IS NULL THEN character ELSE overlay_character END) IS NULL');
    else{where.push('(CASE WHEN overlay IS NULL THEN visual_group ELSE overlay_group END)=?');values.push(q.get('visual_group')!)}}
  const join=`FROM (${selection})`;
  const [count,rows]=await env.DB.batch([
    env.DB.prepare(`SELECT count(*) AS n ${join} WHERE ${where.join(' AND ')}`).bind(...values),
    env.DB.prepare(`SELECT * ${join} WHERE ${where.join(' AND ')} ORDER BY id LIMIT ? OFFSET ?`).bind(...values,limit,offset),
  ]);
  const items=[];
  // Bound simultaneous R2 streams; a corpus page may contain 200 records.
  for(let i=0;i<rows.results.length;i+=8){
    items.push(...await Promise.all((rows.results.slice(i,i+8) as (CorpusRow&{overlay:string|null})[])
      .map(async row=>row.overlay?parse(row.overlay):await corpusData(env,row))));
  }
  const info=await known(env,data.char),familyCode=data.grapheme?.code_point||data.code_point;
  const familyCounts=await env.DB.prepare(`SELECT count(*) AS total,sum(written IS NULL) AS unassigned FROM (
    SELECT CASE WHEN u.id IS NULL THEN c.character ELSE u.character END AS written
      FROM corpus_units c LEFT JOIN units u ON c.id=u.id WHERE c.family=? AND (u.id IS NULL OR u.family=c.family)
    UNION ALL SELECT u.character AS written FROM units u JOIN corpus_units c ON c.id=u.id
      WHERE u.origin='corpus' AND u.family=? AND c.family IS NOT u.family
    )`).bind(familyCode,familyCode).first<{total:number;unassigned:number}>();
  return {...data.candidates,code_point:data.code_point,total:(count.results[0] as {n:number}).n,
    available:items.length,items,scope:family?'grapheme':'character',status:'ok',
    visual_analysis:info.detail.visual_analysis,family_total:familyCounts?.total||0,unassigned_count:familyCounts?.unassigned||0};
}
// A document's characters in source order. The primary key serves the filter and the order, and each
// row joins its unit by id: a published unit gives its current reading, state and revision.
export const documentCharactersQuery = () => `SELECT c.unit,c.data,u.character,u.state,u.revision,json_extract(u.data,'$.issue') AS issue
  FROM document_characters c LEFT JOIN units u ON u.id=c.unit WHERE c.document=? ORDER BY c.ord`;
// Other sites read this listing from the browser, so it is served to any origin, errors included. The
// edge copy is keyed by the publication and the latest review, so a review makes a new key; browsers
// revalidate every time. A refresh that writes `units` directly shows once the edge copy expires.
const OPEN = { 'access-control-allow-origin': '*' };
async function documentCharacters(env: Env, url: URL, document: string, ctx: ExecutionContext) {
  const version = await env.DB.prepare("SELECT (SELECT value FROM metadata WHERE key='published_at') AS published,(SELECT max(rowid) FROM events) AS event")
    .first<{ published: string | null; event: number | null }>();
  const key = new Request(`${url.origin}${url.pathname}?v=${encodeURIComponent(`${version?.published ?? ''}:${version?.event ?? 0}`)}`);
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
  const body = JSON.stringify({ document, published_at: version?.published ? JSON.parse(version.published) : null, characters });
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
  if(answer.character && !single(literal(answer.character)))throw new Problem(422,'Choose one written character.');
  if(answer.verdict==='wrong' && answer.character && literal(answer.character)===current.written_character)
    throw new Problem(422,'Choose a different character or a different issue.');
  if(answer.box) throw new Problem(422,'Crop geometry changes are queued as crop issues on this publication.');
}
async function submit(env: Env, request: Request, target?: string) {
  const input=await body(request);
  const corpus=target==='@corpus';
  if(corpus)target=text(input.identity,512,'corpus identity',true)!;
  const id=text(input.id,64,'submission id',true)!, actor=text(input.client_id,128,'reviewer',true)!;
  if(!/^[0-9a-f-]{36}$/i.test(id))throw new Problem(422,'Invalid submission id.');
  // A retry is the same submission whatever else came on screen meanwhile: the seen crops are left
  // out of the signature, as the local server compares only the answers, and the first result stands.
  const {seen:_,skipped:__,...signed}=input;
  const signature=canonical({target:target||null,input:signed});
  const key=actor+':'+id;
  const previous=await env.DB.prepare('SELECT request,response FROM submissions WHERE id=?').bind(key).first<{request:string;response:string}>();
  if(previous){if(previous.request!==signature)throw new Problem(409,'This submission was already saved with different answers.');return parse(previous.response)}
  const round=!target;
  if(round)text(input.label,32,'label',true);
  const {answers,seen,skipped}=validRound(input,target);
  const changes=[];
  // Corpus glyphs this submission names for the first time; each gets its `units` row first.
  const fresh:(UnitRow&{fresh:CorpusRow})[]=[];
  const at=new Date().toISOString();
  for(const answer of answers){
    text(answer.id,512,'character id',true);
    const row=await unit(env,answer.id), stored=parse(row.data), glyph=row.origin==='corpus';
    const current:Json={...stored,category:row.category||categoryOf(stored.label)};
    row.data=JSON.stringify(current);
    validateAnswer(answer,current,round,glyph);
    if(answer.reading&&!single(answer.reading)){
      const identity=answer.character||current.written_character||current.label;
      const registered=await known(env,identity).catch(()=>null);
      if(!registered?.data.ligature?.reading||hira(registered.data.ligature.reading)!==hira(answer.reading))
        throw new Problem(422,'Use the registered ligature reading or one character.');
    }
    if(glyph&&(!current.proxyable||(current.identity_status==='unassigned'&&answer.verdict==='match')))
      throw new Problem(422,'Choose a written character or report an issue.');
    if(round&&(!row.quiz||current.label!==input.label))throw new Problem(409,'This round changed. Reload it.');
    const written=answer.character?literal(answer.character):null;
    // A corrected character carries its reading along unless one was typed: い corrected to り reads り.
    const derived=written&&!answer.reading?readingFrom((await known(env,written).catch(()=>null))?.data):null;
    const reading=answer.reading || (answer.issue==='reading'&&answer.correction&&single(answer.correction)?answer.correction:null)
      || (derived&&derived!==current.reading?derived:null);
    const resolved=answer.verdict==='match'||Boolean(answer.issue==='character'&&written)||Boolean(answer.issue==='reading'&&reading);
    const family=written?(await known(env,written).catch(()=>null))?.data.grapheme?.code_point:null;
    const next:Json={...current,revision:current.revision+1,state:resolved?'checked':'flagged',
      ...(written?{label:written,char:written,code_point:cp(written),written_character:written,identity_status:'assigned',identity_basis:'human_review',script:/\p{Script=Katakana}/u.test(written)?'katakana':/\p{Script=Hiragana}/u.test(written)?'hiragana':/\p{Script=Han}/u.test(written)?'han':/\p{Script=Hangul}/u.test(written)?'hangul':'symbol'}:{}),
      ...(written?{grapheme:family||cp(written),visual_group:null,category:categoryOf(written)}:{}),
      ...(reading?{reading}:{}),issue:resolved?null:answer.issue};
    const snapshot={...parse(row.snapshot),character:compact(row)};
    const evidence={kind:round?'visual-quiz':'character-review',...(round?{round:id,label:input.label}:{}),
      request:input,verdict:answer.verdict,issue:answer.issue||null,note:answer.note||'',
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
  const result=corpus?{...(changes[0].next),origin:'corpus',event:changes[0].event}
    :{id,results:[...changes.map(c=>({id:c.event.id,target_id:c.row.id,field:'review',revision:c.next.revision,review:c.event})),
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
    // Restore queue eligibility from the record the undo restores.
    statements.push(env.DB.prepare('UPDATE units SET quiz=? WHERE id=?').bind(dealable(current.origin,parse(r.before_data))?1:0,r.target));
    results.push({id:event.id,target_id:r.target,revision:restored.revision,review:event});
  }
  statements.push(env.DB.prepare('UPDATE submissions SET undone=1 WHERE id=?').bind(key));
  try{await env.DB.batch(statements)}catch(error){if(String(error).includes('review_revision_conflict'))throw new Problem(409,'A later review changed this crop.');throw error}
  return {id,results};
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
export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url),path=url.pathname,q=url.searchParams;
    try{
      if(request.method==='POST'){
        if(path==='/atlas/corpus/reviews')return json(await submit(env,request,'@corpus'));
        if(path==='/atlas/rounds')return json(await submit(env,request));
        const undone=path.match(/^\/atlas\/rounds\/([^/]+)\/undo$/);
        if(undone)return json(await undo(env,request,decodeURIComponent(undone[1])));
        const edit=path.match(/^\/(?:atlas\/characters|layers\/units)\/([^/]+)$/);
        if(edit)return json(await submit(env,request,decodeURIComponent(edit[1])));
        throw new Problem(404,'Unknown endpoint.');
      }
      if(!['GET','HEAD'].includes(request.method))throw new Problem(405,'Method not allowed.');
      if(path==='/health')return json({ok:true,published_at:await meta(env,'published_at')});
      const image=path.match(/^\/atlas\/media\/([a-f0-9]{64})\.webp$/);
      if(image)return await media(env,request,image[1],ctx);
      if(path==='/atlas')return json(await catalogue(env,q));
      if(path==='/atlas/corpus/character')return json(parse((await unit(env,q.get('id')||'')).data));
      if(path==='/atlas/collection/status')return json(await meta(env,'collection'));
      const document=path.match(/^\/atlas\/documents\/([^/]+)\/characters$/);
      if(document){let id:string;try{id=decodeURIComponent(document[1])}catch{throw new Problem(404,'No characters are published for this document.')}
        return await documentCharacters(env,url,id,ctx)}
      const visualSample=path.match(/^\/layers\/visual-groups\/samples\/([^/]+)\/image$/);
      if(visualSample){const data=parse((await unit(env,decodeURIComponent(visualSample[1]))).data);
        if(!data.image)throw new Problem(404,'Image not found.');
        return Response.redirect(new URL(data.image,url).href,302)}
      if(path==='/atlas/reviews'||path==='/atlas/reviews.json')return json(await reviews(env,q.get('include_processed')==='true'),200,
        path.endsWith('.json')?{'content-disposition':'attachment; filename="atlas-character-reviews.json"'}:{});
      const character=path.match(/^\/atlas\/characters\/([^/]+)(\/suggestions(?:\/context)?)?$/);
      if(character){const row=await unit(env,decodeURIComponent(character[1]));
        if(character[2]){
          if(q.get('revision')!==String(row.revision)||q.get('image_sha256')!==parse(row.data).image_sha256)throw new Problem(409,'Character changed.');
          return json(parse(character[2].endsWith('/context')?row.context:row.visual));
        }
        return json(parse(row.data));
      }
      if(path==='/layers/suggest')return json(await suggest(env,q));
      if(path==='/layers/search'){const found=await suggest(env,q);return json({...found,results:found.items,match:found.items[0]||null})}
      const layer=path.match(/^\/layers\/characters\/([^/]+)$/);
      if(layer){const value=decodeURIComponent(layer[1]),{detail}=await known(env,value);const found=await occurrences(env,value,q);
        return json({...detail,query:detail.code_point,samples:found.items,occurrences:{...found.counts,filtered:found.total}})}
      if(path==='/layers/occurrences')return json(await occurrences(env,q.get('code_point')||'',q));
      if(path==='/layers/candidates'){const found=await occurrences(env,q.get('code_point')||'',q,'corpus');
        return json({...found,glyphs:found.total,glyph_items:found.items,retry:false})}
      if(path==='/layers/gallery'){
        const limit=integer(q,'limit',24,96),seed=integer(q,'seed',0,2147483647)%268435456;
        const rows=await env.DB.prepare('SELECT * FROM corpus_units WHERE shuffle>=? ORDER BY shuffle LIMIT ?').bind(seed,limit).all<CorpusRow>();
        if(rows.results.length<limit){const more=await env.DB.prepare('SELECT * FROM corpus_units WHERE shuffle<? ORDER BY shuffle LIMIT ?').bind(seed,limit-rows.results.length).all<CorpusRow>();rows.results.push(...more.results)}
        const overlays=rows.results.length?await env.DB.prepare(`SELECT id,data FROM units WHERE id IN (${rows.results.map(()=>'?').join(',')})`)
          .bind(...rows.results.map(r=>r.id)).all<{id:string;data:string}>():{results:[]};
        const current=new Map(overlays.results.map(r=>[r.id,r.data]));
        const items=[];for(let i=0;i<rows.results.length;i+=8)items.push(...await Promise.all(rows.results.slice(i,i+8).map(async r=>
          current.has(r.id)?parse(current.get(r.id)!):await corpusData(env,r))));
        return json({status:'ok',available:items.length,items})}
      if(path==='/layers/summary')return json(await meta(env,'corpus_index'));
      if(path==='/layers/graphemes'||path==='/layers/ligatures'){
        const selector=path.endsWith('ligatures')?"json_extract(data,'$.ligature') IS NOT NULL":"json_array_length(json_extract(data,'$.grapheme.members'))>1";
        const rows=await env.DB.prepare(`SELECT data FROM characters WHERE ${selector} LIMIT ? OFFSET ?`).bind(integer(q,'limit',200,500),integer(q,'offset',0)).all<{data:string}>();
        return json({items:rows.results.map(r=>parse(r.data)),total:rows.results.length})}
      if(path==='/atlas/corpus/reviews'){
        const rows=await env.DB.prepare("SELECT * FROM units WHERE origin='corpus' AND state='flagged' ORDER BY id LIMIT 96").all<UnitRow>();
        return json({items:rows.results.map(compact),total:rows.results.length})}
      if(path.startsWith('/atlas')||path.startsWith('/layers')||path.startsWith('/images/'))throw new Problem(404,'Unknown endpoint.');
      return await env.ASSETS.fetch(request);
    }catch(error){
      const open=path.startsWith('/atlas/documents/')?OPEN:{};
      if(error instanceof Problem)return json({detail:error.message},error.status,open);
      console.error(JSON.stringify({event:'request_failed',path,error:error instanceof Error?error.name:'unknown'}));
      return json({detail:'The request could not be completed. Please retry.'},503,open);
    }
  },
} satisfies ExportedHandler<Env>;
