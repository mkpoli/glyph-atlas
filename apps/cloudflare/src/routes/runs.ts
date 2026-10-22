// Runs of characters on glyph-atlas-v3 (decision record 0005): Explore's grid of the most frequent
// pairs and trigrams (`/atlas/ngrams/:n`), one run's occurrences (`/atlas/runs`) and the runs near it
// (`/atlas/runs/related`), and the open listing of a book's characters (`/atlas/documents/:doc/characters`).
// Runs, their counts and how their crops are written live in glyph-atlas-v3-runs (ATLAS_RUNS,
// migrations 0001 and 0002); the counts a publication makes are R2 answers under `v3/runs/`
// (scripts/atlas3/run_answers.py); their crops, books and dates are in glyph-atlas-v3 (ATLAS) and the page
// bundles. A book is named by its key at the URL boundary and by its integer id in both databases. Which
// runs a crop is in moves at a publication; how its crops are written follows reviews (`run_written`).
import { bundle, cropItems, pageOf, publicId, slotOf, type Entry } from '../atlas3';
import { catalogueStamps, catalogueVersion, publicationKey } from '../atlas3Stamps';
import { currentPointer } from '../atlas3Gallery';
import { Problem } from '../busy';
import { bookDating } from '../dating';
import { formed, type UnitForm } from '../forms';
import { honkokuPage } from '../honkoku';
import { MARKS, WRITTEN, redrawLimits, writtenRecord } from '../cropRecords';
import { integer, text } from '../input';
import { readRecords, recordKey } from '../records';

type Json = any;
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
// How long the edge keeps a count or a run's page under one catalogue version.
const FACETS_TTL = 3600;
const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
// SQLite orders text by its bytes, which is code point order; JavaScript's `<` is UTF-16 order.
function byCodePoint(a: string, b: string): number {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}

// A book by its key: one unique-key probe. A key no book has names nothing (-1), so every read of it
// is empty.
export const bookIdQuery = () => 'SELECT id FROM books WHERE key=?';
async function bookId(env: Env, key: string | null): Promise<number | null> {
  if (key === null) return null;
  return (await env.ATLAS.prepare(bookIdQuery()).bind(key).first<{ id: number }>())?.id ?? -1;
}

// ---- Explore's grid.

// The pair and trigram frequencies Explore's grid shows: runs of crops and of corpus glyphs that follow
// each other on a line, counted by their graphemes (ん𛁅 and ん𛁈 together as んし), most frequent first,
// with how many written forms each gathers. The site's and each book's are the publication's answers
// (`v3/runs/grid-<n>`, `v3/runs/books/<book>-<n>`), so the edge keeps one copy per publication, length
// and book. A run is shown down the page where most of its occurrences are written that way.
const NGRAMS_MAX = 480, NGRAM_SIZES = new Set(['2', '3']);
export function ngramSize(value: string) {
  if (!NGRAM_SIZES.has(value)) throw new Problem(404, 'Runs of two or three characters are counted.');
  return Number(value);
}
/** A runs answer of the publication, or null where it has none. */
export async function runAnswer(env: Env, name: string): Promise<Json | null> {
  const object = await env.MEDIA.get(`v3/runs/${name}.json.gz`);
  return object ? await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).json() : null;
}
const publication = async (env: Env) => publicationKey(await catalogueStamps(env));
async function ngrams(env: Env, ctx: ExecutionContext, url: URL, size: number) {
  const document = text(url.searchParams.get('document'), 256, 'document');
  const key = new Request(`${url.origin}/atlas/ngrams/${size}?document=${encodeURIComponent(document ?? '')}&v=${encodeURIComponent(await publication(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const book = await bookId(env, document);
  const rows = (book === null ? await runAnswer(env, `grid-${size}`) : await runAnswer(env, `books/${book}-${size}`)) as (string | number)[][] | null;
  const body = { items: (rows ?? []).map(([text, n, down, forms]) => ({ text, n, vertical: 2 * (down as number) >= (n as number), forms: book === null ? forms : null })),
    limit: NGRAMS_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}
/** The forms shard of runs of `size` that begin with `graphemes`' first code point: for each graphemes,
 *  how many written forms its runs gather and the most frequent. */
export const formsShard = (size: number, graphemes: string) => `forms/${size}-${graphemes.codePointAt(0)!.toString(16)}`;

// ---- One run's occurrences.

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
// split into graphemes, one crop each, as a label holds one, and each is folded to its grapheme family's
// head (`runGraphemes`), so a run is found by its graphemes whichever form each crop is written in; the
// crops shown are as written. A run of two or three is one row of `runs`. A longer one starts from one
// of its trigrams, the rarest (`runProbeQuery`), and reaches the rest of its crops a pair at a time:
// publication writes a pair only where two crops follow each other and stand near, so a chain of pairs
// holds the runs a longer row would. The first row is read along the primary key (or `run_book` for a
// book), so occurrences come in that order: how its first crop's letterforms were made (shaped by hand
// first), then that crop's shuffle and id. Every other row is found by its crop: rightwards through
// `run_first`, leftwards through `run_second`, whose condition (`size = 2`) the query repeats as written;
// the other tests are kept off those indexes (`+`). The publisher removes a run with a crop it retires,
// so every crop a row names is live; one the crop index or its bundle no longer holds leaves its
// occurrence off the page. Counting stops past RUN_COUNT_MAX, which is as deep as a run pages, and the
// first page alone counts. A page holds up to RUN_PAGE_CROPS crops, so a longer run comes in fewer
// occurrences at a time. RUN_MAX keeps a run inside one context render: a render reaches five character
// sizes along a column, so its middle crop's holds about eleven, and `runPage` finds the run in it. A run
// of two or three narrows to one of the written forms its graphemes gather (`form`), which its first page
// lists (a forms shard, or `bookFormsQuery`).
// How a run's letterforms were made, as `runs.hand` numbers its first crop: shaped by hand (handwritten,
// inscribed, cut or drawn for the page, or written in a running or cursive hand), not known, and set from
// type. A run's page lists them in this order and narrows to one (`hand`).
export const HAND_ORDER: Record<string, number> = { hand: 0, unknown: 1, type: 2 };
const HAND_NAMES = Object.keys(HAND_ORDER);
function handGroup(q: URLSearchParams): number | null {
  const value = q.get('hand');
  if (!value || value === 'all') return null;
  if (!Object.hasOwn(HAND_ORDER, value)) throw new Problem(422, 'Invalid group.');
  return HAND_ORDER[value];
}
const RUN_FORMS_MAX = 24, RUN_MAX = 8, RUN_PAGE_CROPS = 192, RUN_COUNT_MAX = 2000, RUN_PROBE_MAX = 5000, RUN_WORKS_MAX = 60, RUN_NEAR_MAX = 12;
const RUN_COLUMNS = ['first', 'second', 'third'];
const segments = new Intl.Segmenter('ja', { granularity: 'grapheme' });
/** The characters of a run, one crop each. */
export const runCharacters = (value: string) => [...segments.segment(value)].map(s => s.segment);
/** How a run of `size` crops is read when its row `a` starts at crop `anchor`: the joins after it, the
 *  crop each member is (`members`), and the first crop of each pair they bind a text to, in the order
 *  they are bound. */
export function runFrom(size: number, anchor: number, book = false) {
  const span = Math.min(size, 3), crops: string[] = [], joins: string[] = [], links: number[] = [];
  for (let i = 0; i < span; i++) crops[anchor + i] = `a.${RUN_COLUMNS[i]}`;
  for (let i = anchor + span - 1; i < size - 1; i++) {
    joins.push(`CROSS JOIN runs r${i} ON r${i}.first=${crops[i]} AND +r${i}.size=2 AND +r${i}.graphemes=?`);
    crops[i + 1] = `r${i}.second`; links.push(i);
  }
  for (let i = anchor - 1; i >= 0; i--) {
    joins.push(`CROSS JOIN runs l${i} ON l${i}.second=${crops[i + 1]} AND l${i}.size=2 AND +l${i}.graphemes=?`);
    crops[i] = `l${i}.first`; links.push(i);
  }
  // A book's rows are its range of `run_book`, named, since the primary key also serves the other terms.
  return { from: [`FROM runs a${book ? ' INDEXED BY run_book' : ''}`, ...joins].join(' '), members: crops, links };
}
// A crop as written in a run: its `run_written` row, else the head its runs' graphemes name, bound.
const writtenAs = (crop: string) => `coalesce((SELECT char FROM run_written WHERE crop=${crop}),?)`;
// A written form (`form`) narrows a run of two or three to the rows written so, each crop tested on each
// row the run's graphemes key reads, by the crop's key.
const runWhere = (book: boolean, hand = false, form = 0) => `${book ? 'a.book=? AND ' : ''}a.size=? AND a.graphemes=?`
  + Array.from({ length: form }, (_, i) => ` AND ${writtenAs(`a.${RUN_COLUMNS[i]}`)}=?`).join('') + `${hand ? ' AND a.hand=?' : ''}`;
// A group of how the letterforms were made (`hand`) and a book narrow the run; `sort=source` lists a
// run by book instead, in the order of the books' ids.
export function runOccurrencesQuery(size: number, anchor: number, book: boolean, hand = false, sort: 'hand' | 'source' = 'hand', form = false) {
  const { from, members } = runFrom(size, anchor, book);
  return `SELECT ${members.map((crop, i) => `${crop} AS m${i}`).join(',')},a.book AS book
    ${from} WHERE ${runWhere(book, hand, form ? size : 0)}
    ORDER BY ${sort === 'source' && !book ? 'a.book,a.first' : 'a.hand,a.shuffle,a.first'} LIMIT ? OFFSET ?`;
}
// A run is counted a group at a time, each count stopping past RUN_COUNT_MAX: the whole run is their sum,
// which passes RUN_COUNT_MAX exactly when the run does.
export function runCountQuery(size: number, anchor: number, book: boolean, form = false) {
  return `SELECT count(*) AS n FROM (SELECT 1 ${runFrom(size, anchor, book).from}
    WHERE ${runWhere(book, true, form ? size : 0)} LIMIT ${RUN_COUNT_MAX + 1})`;
}
/** The books a run is in, with their occurrences, counted among the first RUN_COUNT_MAX. */
export function runWorksQuery(size: number, anchor: number, form = false) {
  return `SELECT b AS book, count(*) AS n FROM (SELECT a.book AS b ${runFrom(size, anchor).from}
    WHERE ${runWhere(false, false, form ? size : 0)} LIMIT ${RUN_COUNT_MAX + 1}) GROUP BY b`;
}
// A query is folded as a run's graphemes are: each character to its grapheme family's head (`graphemes`,
// by its unique text); one the site does not know stands for itself.
export const runGraphemesQuery = () => `SELECT g.text AS character,coalesce(h.text,g.text) AS head FROM graphemes g
  LEFT JOIN graphemes h ON h.id=g.head WHERE g.text IN (SELECT value FROM json_each(?))`;
async function runGraphemes(env: Env, parts: string[]): Promise<string[]> {
  const heads = new Map((await env.ATLAS.prepare(runGraphemesQuery()).bind(JSON.stringify(parts)).all<{ character: string; head: string }>())
    .results.map(row => [row.character, row.head]));
  return parts.map(part => heads.get(part) ?? part);
}
// The written forms a run of two or three gathers, each with its count, most frequent first: the site's
// from the publication's forms shard (`formsShard`), a book's grouped from a capped read of the book's
// runs of those graphemes, as its books are (`runWorksQuery`), each crop as it is written now.
export const bookFormsQuery = (size: number) => `SELECT text, count(*) AS n FROM (SELECT
  ${Array.from({ length: size }, (_, i) => writtenAs(RUN_COLUMNS[i])).join('||')} AS text
  FROM runs INDEXED BY run_book WHERE size=? AND graphemes=? AND book=? LIMIT ${RUN_COUNT_MAX + 1})
  GROUP BY text ORDER BY n DESC,text LIMIT ${RUN_FORMS_MAX}`;
/** How many rows of a trigram there are, counting no further than RUN_PROBE_MAX. */
export function runProbeQuery(book: boolean) {
  return `SELECT count(*) AS n FROM (SELECT 1 FROM runs${book ? ' INDEXED BY run_book' : ''} WHERE ${book ? 'book=? AND ' : ''}size=3 AND graphemes=? LIMIT ${RUN_PROBE_MAX})`;
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

// The books a page and its works name: key and title, one primary-key probe each.
export const booksQuery = () => 'SELECT id,key,title FROM books WHERE id IN (SELECT value FROM json_each(?))';
// Each page's revision, one primary-key probe each, so the bundles are read once for the records and
// the places of the crops.
export const pageRevsQuery = () => 'SELECT id,rev FROM pages WHERE id IN (SELECT value FROM json_each(?))';
// A corpus glyph's forms tool decision: its `form_glyphs` row (its own, or a cluster decision of an earlier
// clustering it carries) when it has one, else its cluster's (`crops.cluster`); a cluster issue 'mixed'
// gives its glyphs no issue. One primary-key probe per glyph and per cluster.
export const corpusFormsQuery = () => `SELECT c.id,coalesce(g.cluster,c.cluster) IS NOT NULL AS clustered,g.decision IS NOT g.carried AS glyph_set,coalesce(k.key,'') AS cluster,
  k.form AS cluster_form,iif(g.crop IS NULL,k.form,g.form) AS form,
  iif(g.crop IS NULL,iif(k.issue='mixed',NULL,k.issue),g.issue) AS issue,iif(g.crop IS NULL,k.character,g.character) AS issue_character,
  iif(g.crop IS NULL,k.written_family,g.written_family) AS written_family
  FROM json_each(?) j JOIN crops c ON c.id=j.value AND c.kind=1 LEFT JOIN form_glyphs g ON g.crop=c.id
  LEFT JOIN form_clusters k ON k.id=coalesce(g.cluster,c.cluster) WHERE c.cluster IS NOT NULL OR g.crop IS NOT NULL`;
// A round's mark (`seen`, `hard`) keeps a crop in the review queue: its listing shows it pending, at the
// revision before the marks that follow its last other event, as the old site never counted marks.
const shownState = (state: string | null) => state === 'seen' || state === 'hard' ? 'pending' : state;
// A flagged crop's issue is its last event's; `reading` is the name before the rename, `character`.
const ISSUE = (crop: string) => `(SELECT i.key FROM events e LEFT JOIN codes i ON i.field='issue' AND i.code=coalesce(e.kept_issue,e.issue)
    WHERE e.crop=${crop} ORDER BY e.revision DESC LIMIT 1)`;
const issueName = (issue: string | null) => issue === 'reading' ? 'character' : issue;
// What reviews say of the flagged and marked crops of a page: one `event_crop` range each.
export const reviewedQuery = () => `SELECT j.value AS crop,${MARKS('c')} AS marks,r.x,r.y,r.w,r.h,
  g.text AS char,h.text AS head,${WRITTEN('c.id')} AS written,
  CASE WHEN s.key='flagged' THEN ${ISSUE('j.value')} END AS issue
  FROM json_each(?) j JOIN crops c ON c.id=j.value JOIN codes s ON s.field='state' AND s.code=c.state
  LEFT JOIN reviews r ON r.crop=c.id LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN graphemes h ON h.id=g.head`;
type Reviewed = { crop: number; marks: number; issue: string | null; x: number | null; y: number | null; w: number | null; h: number | null;
  char: string | null; head: string | null; written: number };

/** Each crop as a run's page shows it, by id: its listing item, its box on the page, and its record's
 *  place on the page (`runPage`). A crop with no listing item is absent. */
async function runCrops(env: Env, ids: number[]): Promise<Map<number, { item: Json; whole: Json }>> {
  const found = new Map<number, { item: Json; whole: Json }>();
  if (!ids.length) return found;
  const pages = [...new Set(ids.map(pageOf))];
  const revs = (await env.ATLAS.prepare(pageRevsQuery()).bind(JSON.stringify(pages)).all<{ id: number; rev: number }>()).results;
  const bundles = new Map(await Promise.all(revs.map(async ({ id, rev }) => [id, await bundle(env, id, rev)] as const)));
  const entries = new Map(ids.flatMap(id => {
    const entry = bundles.get(pageOf(id))?.bySlot.get(slotOf(id));
    return entry ? [[id, entry] as const] : [];
  }));
  const pointer = (entry: Entry) => ({ object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] });
  const records = await readRecords(env, [...entries.values()].filter(entry => entry.record_place).map(pointer));
  // Each crop's whole record, in place of its bundle entry, so the listing reads nothing again.
  const given = new Map<number, Entry>();
  for (const [id, entry] of entries) {
    if (!entry.record_place) { given.set(id, entry); continue; }
    const record = records.get(recordKey(pointer(entry)));
    if (record) given.set(id, { ...record, slot: entry.slot, id: entry.id, ...(entry.retired ? { retired: true } : {}) });
  }
  const listed = [...given.keys()];
  const [items, decided] = await Promise.all([cropItems(env, listed, given),
    env.ATLAS.prepare(corpusFormsQuery()).bind(JSON.stringify(listed)).all<Omit<UnitForm, 'id'> & { id: number }>()]);
  const forms = new Map(decided.results.map(row => [row.id, { ...row, id: publicId(row.id) } as UnitForm]));
  const crop = new Map(listed.map(id => [publicId(id), id]));
  const reviewed = items.map(item => crop.get(item.id)!);
  const since = new Map(reviewed.length ? (await env.ATLAS.prepare(reviewedQuery()).bind(JSON.stringify(reviewed))
    .all<Reviewed>()).results.map(row => [row.crop, row]) : []);
  for (const item of items) {
    const id = crop.get(item.id)!, whole = given.get(id)!, review = since.get(id);
    let shown = item.origin === 'corpus' ? formed(item, forms.get(id) ?? null, { codePoints }) : item;
    if (review?.written && review.char) shown = writtenRecord(shown, review.char, review.head);
    found.set(id, { whole, item: { ...shown, state: shownState(shown.state), revision: shown.revision - (review?.marks ?? 0),
      // A corpus glyph's record names the page of its source; its row names the page the layout files it on.
      page_id: whole.page_id ?? shown.page_id,
      ...(review && review.x !== null ? { box: { x: review.x, y: review.y, w: review.w, h: review.h }, box_pending: true } : {}),
      ...(item.state === 'flagged' ? { issue: issueName(review?.issue ?? null) } : {}),
      ...(item.origin === 'local' ? { crop_editable: Boolean(redrawLimits(whole)) } : {}) } });
  }
  return found;
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
  const size = parts.length;
  const [folded, book] = await Promise.all([runGraphemes(env, parts), bookId(env, document)]);
  const scope = book === null ? [] : [book];
  timing.step('graphemes');
  const span = (from: number, length: number) => folded.slice(from, from + length).join('');
  const db = env.ATLAS_RUNS;
  let anchor = 0;
  if (size > 3) {
    const probes = await db.batch(Array.from({ length: size - 2 }, (_, i) =>
      db.prepare(runProbeQuery(book !== null)).bind(...scope, span(i, 3)))) as D1Result<{ n: number }>[];
    const counts = probes.map(probe => probe.results[0].n);
    anchor = counts.indexOf(Math.min(...counts));
    timing.step('probe');
  }
  const { links } = runFrom(size, anchor);
  // Each crop of a written form is bound after the head its run's graphemes name there.
  const written = form ? runCharacters(form).flatMap((c, i) => [folded[i], c]) : [];
  const bound = [...links.map(i => span(i, 2)), ...scope, Math.min(size, 3), span(anchor, Math.min(size, 3)), ...written];
  const styled = (n: number) => [...bound, n];
  const occurrences = db.prepare(runOccurrencesQuery(size, anchor, book !== null, group !== null, sort, Boolean(form))).bind(...(group === null ? bound : styled(group)), limit, offset);
  // The first page also counts each group, which counts the run as a whole or in the group chosen, names
  // its books, and lists the written forms a run of two or three gathers, whichever form is chosen.
  const first = offset ? [] : [
    ...HAND_NAMES.map(name => db.prepare(runCountQuery(size, anchor, book !== null, Boolean(form))).bind(...styled(HAND_ORDER[name]))),
    db.prepare(runWorksQuery(size, anchor, Boolean(form))).bind(...links.map(i => span(i, 2)), Math.min(size, 3), span(anchor, Math.min(size, 3)), ...written),
    ...(size <= 3 && book !== null ? [db.prepare(bookFormsQuery(size)).bind(...folded, size, span(0, size), book)] : []),
  ];
  const [[page, ...rest], shard] = await Promise.all([db.batch([occurrences, ...first]) as Promise<D1Result<any>[]>,
    !offset && size <= 3 && book === null ? runAnswer(env, formsShard(size, span(0, size))) : null]);
  timing.step('rows');
  const found = page.results as ({ book: number } & Record<`m${number}`, number>)[];
  const hands = rest.slice(0, HAND_NAMES.length).map(result => result.results[0] as { n: number });
  const works = rest[HAND_NAMES.length]?.results as { book: number; n: number }[] | undefined;
  const forms = !offset && size <= 3 ? book === null
    ? ((shard?.[span(0, size)]?.[1] ?? []) as [string, number][]).map(([text, n]) => ({ text, n }))
    : rest[HAND_NAMES.length + 1].results as { text: string; n: number }[] : null;
  // The crops of one run stand on one page, so they share their book's dates.
  const crops = [...new Set(found.flatMap(row => Array.from({ length: size }, (_, i) => row[`m${i}`])))];
  const named = [...new Set([...found.map(row => row.book), ...(works ?? []).map(row => row.book)])];
  const [dating, records, books] = await Promise.all([timing.beside('dating', bookDating(env, found.map(row => row.book))),
    timing.beside('records', runCrops(env, crops)),
    timing.beside('works', env.ATLAS.prepare(booksQuery()).bind(JSON.stringify(named)).all<{ id: number; key: string; title: string | null }>()
      .then(r => new Map(r.results.map(row => [row.id, row]))))]);
  timing.step('reads');
  const items = found.flatMap(row => {
    const members = Array.from({ length: size }, (_, i) => records.get(row[`m${i}`]));
    if (!members.every(Boolean)) return [];
    const dated = dating.get(row.book), document = books.get(row.book)?.key ?? null;
    // An occurrence transcribed on みんなで翻刻 links to its page there, as a crop's inspector does: the
    // first of its crops that names one.
    const honkoku = members.map(c => honkokuPage(c!.item, document ?? c!.item.source?.document_id ?? null)).find(Boolean) ?? null;
    return [{ crops: members.map(c => ({ ...c!.item, crop_box: c!.whole.crop_box ?? null, dating: dated ?? {} })),
      vertical: true, page: runPage(members.map(c => c!.whole)), honkoku_url: honkoku }];
  });
  const chosen = group === null ? hands : hands.slice(group, group + 1);
  const counted = hands.length ? { n: chosen.reduce((sum, h) => sum + h.n, 0) } : undefined;
  const listedWorks = works && works.map(row => ({ id: books.get(row.book)?.key ?? null, title: books.get(row.book)?.title ?? null, count: row.n }))
    .filter((work): work is { id: string; title: string | null; count: number } => work.id !== null)
    .sort((a, b) => b.count - a.count || byCodePoint(a.id, b.id)).slice(0, RUN_WORKS_MAX);
  const body = { text: value, size, document, form, hand: group === null ? 'all' : HAND_NAMES[group], sort, next_offset: offset + found.length, items,
    ...(counted && { total: Math.min(counted.n, RUN_COUNT_MAX), more: counted.n > RUN_COUNT_MAX, vertical: true,
      hands: Object.fromEntries(HAND_NAMES.map((name, i) => [name, Math.min(hands[i].n, RUN_COUNT_MAX)])), hand_groups: HAND_NAMES, works: listedWorks, forms }) };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}

// ---- The runs near one.

// The runs near one, to move between them: the shorter runs a trigram or four holds, the trigrams a pair
// begins, and the runs of the same length that begin with the same characters (a pair's first, a
// trigram's first two), most frequent first. The run is folded to its graphemes as its occurrences are
// (`runGraphemes`), and a prefix is one key range of the site's counts (`run_stats`), a row per grapheme
// sequence rather than one per occurrence, with the forms each gathers from the publication's forms
// shard, which all of one length share since they begin alike. The answer is kept at the edge per
// publication like the counts.
export const runRangeQuery = () => `SELECT graphemes AS text,n,2*down>=n AS vertical FROM run_stats
  WHERE size=? AND graphemes>=? AND graphemes<? AND graphemes<>? AND graphemes<>'' ORDER BY n DESC,graphemes LIMIT ${RUN_NEAR_MAX}`;
export const runHasQuery = () => 'SELECT graphemes AS text FROM run_stats WHERE size=? AND graphemes=?';
async function runRelated(env: Env, ctx: ExecutionContext, url: URL) {
  const value = text(url.searchParams.get('text'), 96, 'text', true)!;
  const parts = runCharacters(value);
  if (parts.length < 2 || parts.length > RUN_MAX) throw new Problem(422, `A run is two to ${RUN_MAX} characters.`);
  const key = new Request(`${url.origin}/atlas/runs/related?${new URLSearchParams({ text: value, v: await publication(env) })}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const db = env.ATLAS_RUNS;
  const size = parts.length, folded = await runGraphemes(env, parts), join = (from: number, to: number) => folded.slice(from, to).join('');
  const beyond = (prefix: string) => prefix + '\u{10FFFF}';
  const near = (length: number, prefix: string, except = '') => db.prepare(runRangeQuery()).bind(length, prefix, beyond(prefix), except);
  const whole = join(0, size);
  const lead = size === 2 ? join(0, 1) : join(0, 2);
  const inside = size === 3 || size === 4 ? [join(0, size - 1), join(1, size)] : [];
  const [longer, siblings, ...held] = await db.batch([
    size === 2 ? near(3, whole) : db.prepare('SELECT 1 WHERE 0'),
    size === 2 || size === 3 ? near(size, lead, whole) : db.prepare('SELECT 1 WHERE 0'),
    ...inside.map(part => db.prepare(runHasQuery()).bind(size - 1, part)),
  ]) as D1Result<any>[];
  const [longerForms, siblingForms] = await Promise.all([size === 2 ? runAnswer(env, formsShard(3, whole)) : null,
    size === 2 || size === 3 ? runAnswer(env, formsShard(size, lead)) : null]);
  const shown = (rows: Json[], shard: Json | null) => rows.map(row => ({ ...row, vertical: Boolean(row.vertical), forms: shard?.[row.text]?.[0] ?? 0 }));
  const body = { text: value, size, lead, inside: held.flatMap(result => result.results.map((row: Json) => row.text as string)),
    longer: size === 2 ? shown(longer.results, longerForms) : [], siblings: size === 2 || size === 3 ? shown(siblings.results, siblingForms) : [], limit: RUN_NEAR_MAX };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': `public, max-age=${FACETS_TTL}` } })));
  return body;
}

// ---- A book's characters, for other sites.

// A book's characters in source order (`book_characters`, migration 0005), each with what the site
// shows of it. Whether the site shows an occurrence with its review state is derived as it is read: a
// collected crop always, a corpus glyph once a round showed, skipped or reviewed it (`seen_crop`, the
// `skips` key, `event_crop`). A shown crop gives its current character, state, revision and issue: a
// round's mark shows as pending, at the revision before the marks that follow its last other event, and
// a flagged crop's issue is its last event's (`reading`, the name before the rename, as `character`).
// One page costs a key range of `book_characters`, one `crops` row per occurrence and those probes.
const CHARACTERS_PAGE = 1000;
export const bookCharactersQuery = () => `SELECT c.ord,c.crop,c.unit,c.data,g.text AS character,s.key AS state,k.revision-${MARKS('k')} AS revision,
  CASE WHEN s.key='flagged' THEN ${ISSUE('c.crop')} END AS issue,
  (k.kind=0 OR (k.kind=1 AND (EXISTS (SELECT 1 FROM seen WHERE crop=c.crop) OR EXISTS (SELECT 1 FROM skips WHERE crop=c.crop)
    OR EXISTS (SELECT 1 FROM events WHERE crop=c.crop)))) AS shown
  FROM book_characters c LEFT JOIN crops k ON k.id=c.crop LEFT JOIN codes s ON s.field='state' AND s.code=k.state
  LEFT JOIN graphemes g ON g.id=k.char WHERE c.book=?1 AND c.ord>=?2 ORDER BY c.ord LIMIT ?3`;
type CharacterRow = { ord: number; crop: number | null; unit: string | null; data: string; character: string | null; state: string | null; revision: number | null;
  issue: string | null; shown: number | null };
// Other sites read this listing from the browser, so it is served to any origin, errors included. The
// edge copy is keyed by the catalogue version, so any review makes a new key; browsers revalidate every
// time.
const OPEN = { 'access-control-allow-origin': '*' };
// A publication is named by the second it was made (`20261019T130411Z`); `published_at` writes it as an
// ISO 8601 time.
const publishedAt = (pub: string) => pub.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/, '$1-$2-$3T$4:$5:$6+00:00');
async function documentCharacters(env: Env, url: URL, document: string, ctx: ExecutionContext) {
  const key = new Request(`${url.origin}${url.pathname}?v=${encodeURIComponent(await catalogueVersion(env))}`);
  const served = (body: BodyInit | null) => new Response(body, { headers: { 'content-type': 'application/json',
    'cache-control': 'no-cache', 'x-content-type-options': 'nosniff', ...OPEN } });
  const cached = await caches.default.match(key);
  if (cached) return served(cached.body);
  const book = await bookId(env, document);
  const rows: CharacterRow[] = [];
  for (let from = 0; ; ) {
    const page = (await env.ATLAS.prepare(bookCharactersQuery()).bind(book, from, CHARACTERS_PAGE).all<CharacterRow>()).results;
    rows.push(...page);
    if (page.length < CHARACTERS_PAGE) break;
    from = page[page.length - 1].ord + 1;
  }
  if (!rows.length) throw new Problem(404, 'No characters are published for this document.');
  const characters = rows.map(r => {
    // An occurrence of a unit the site never had is named by its text id.
    const d = JSON.parse(r.data), unit = r.crop === null ? r.unit : publicId(r.crop);
    if (!r.shown) return { ...d, unit, atlas: false };
    const label = r.character ?? d.label, state = shownState(r.state);
    // A label a reviewer changed or confirmed on the site is the review's, not its first source's.
    const source = label !== d.label || state === 'checked' ? 'review' : d.source;
    return { ...d, unit, atlas: true, label, source, state, revision: r.revision, issue: issueName(r.issue) };
  });
  const pointer = await currentPointer(env).catch(() => null);
  const body = JSON.stringify({ document, published_at: pointer ? publishedAt(pointer.pub) : null, characters });
  ctx.waitUntil(caches.default.put(key, new Response(body, { headers: { 'content-type': 'application/json', 'cache-control': 'public, max-age=60' } })));
  return served(body);
}

// ---- A review's write.

/** The write a review makes when it changes a crop's written character: the crop's `run_written` row,
 *  one row in glyph-atlas-v3-runs, so its runs show it as written at once. A character that is its
 *  runs' head is written too and reads the same; the next publication drops the row. An unchanged row
 *  is not written. */
export const writtenStatement = (env: Env, crop: number, char: string) => env.ATLAS_RUNS.prepare(
  'INSERT INTO run_written(crop,char) VALUES(?,?) ON CONFLICT(crop) DO UPDATE SET char=excluded.char WHERE char<>excluded.char').bind(crop, char);

/** The routes of this module, or null for a path it does not serve. */
export async function runsRoute(env: Env, ctx: ExecutionContext, url: URL): Promise<Response | null> {
  const path = url.pathname;
  const counted = path.match(/^\/atlas\/ngrams\/(\d+)$/);
  if (counted) return json(await ngrams(env, ctx, url, ngramSize(counted[1])));
  if (path === '/atlas/runs/related') return json(await runRelated(env, ctx, url));
  if (path === '/atlas/runs') {
    const timing = new Timing(), body = await runOccurrences(env, ctx, url, timing);
    return json(body, 200, { 'server-timing': timing.header() });
  }
  const document = path.match(/^\/atlas\/documents\/([^/]+)\/characters$/);
  if (document) {
    let id: string;
    try { id = decodeURIComponent(document[1]) } catch { throw new Problem(404, 'No characters are published for this document.') }
    return await documentCharacters(env, url, id, ctx);
  }
  return null;
}
