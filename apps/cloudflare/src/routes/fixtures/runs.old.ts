// The run and book-character routes as origin/main answers them from the old database (`DB`), kept
// only to compare the new routes with (runs.parity.test.ts). Copied from origin/main's index.ts with the
// helpers they call; the catalogue version is the test's own.
import { Problem } from '../../busy';
import { datingOf } from '../../dating';
import { formed, FORM_COLUMNS, type UnitForm } from '../../forms';
import { honkokuPage } from '../../honkoku';
import { compose } from '../../index';
import { readRecords, recordKey, type RecordPointer } from '../../records';

type Json = any;
const parse = (value: string): Json => JSON.parse(value);
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const formTools = { codePoints: cp };
const FACETS_TTL = 3600;
export const catalogueVersion = async (_env: Env) => 'old';
function text(value: unknown, max: number, name: string, required = false): string | null {
  if (value == null && !required) return null;
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw new Problem(422, `Invalid ${name}.`);
  return compose(value.trim());
}
function integer(q: URLSearchParams, key: string, fallback: number, max = 1000000) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) throw new Problem(422, `Invalid ${key}.`);
  return n;
}
async function meta(env: Env, key: string): Promise<any> {
  const row = await env.DB.prepare('SELECT value FROM metadata WHERE key=?').bind(key).first<{ value: string }>();
  return row ? JSON.parse(row.value) : null;
}
function listing(d: Json): Json {
  const { text, line, context_image, context_box, crop_box, ...rest } = d;
  return rest;
}
async function corpusRecords(env: Env, pointers: RecordPointer[]): Promise<Map<string, Json>> {
  const raw = await readRecords(env, pointers);
  const ids = [...new Set([...raw.values()].map(r => r.id).filter(Boolean))];
  const forms = new Map(ids.length ? (await env.DB.prepare(`SELECT ${FORM_COLUMNS} FROM form_units WHERE id IN (SELECT value FROM json_each(?))`)
    .bind(JSON.stringify(ids)).all<UnitForm>()).results.map(row => [row.id, row] as const) : []);
  return new Map([...raw].map(([key, record]) => [key, formed(record, forms.get(record.id) ?? null, formTools)]));
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
function runCut(service: string | null | undefined, render: string, region: Rect): string | null {
  if (!service || !/^https?:\/\//i.test(service) || !render.startsWith(service + '/')) return null;
  const scale = Math.min(1, CUT_WIDTH / region.w, CUT_HEIGHT / region.h);
  return `${service}/${region.x},${region.y},${region.w},${region.h}/${Math.max(1, Math.round(region.w * scale))},/0/default.jpg`;
}
function runPage(crops: Json[]): { image: string; box: Rect; region: Rect; cut: string | null } | null {
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

export const oldRoutes = { ngrams, ngramSize, runOccurrences, runRelated, documentCharacters, Timing };
