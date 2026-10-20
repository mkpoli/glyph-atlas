// A crop's pages on glyph-atlas-v3: its record for the inspector with its form, word and book dates, its
// evidence versions, its citation, its neighbours on its line, its model votes, its image, and the source
// page it sits on. Crops are named by public id; an old text id or a retired crop's id redirects
// (`atlas3Crops.ts`).
import { bundle, publicId, pageOf, slotOf } from '../atlas3';
import { cropParam, cropQueryParam, movedPathResponse, movedQueryResponse } from '../atlas3Crops';
import { catalogueVersion } from '../atlas3Stamps';
import { cropVotes, restoreVotes } from '../atlas3Votes';
import { Problem } from '../busy';
import { bibtex, citedVersion, cropEntry, csl, FORMATS, hayagriva, type Day, type Format } from '../citation';
import { axisOf } from '../dating';
import { honkokuPage } from '../honkoku';
import { cropForms, cropWords } from '../cropPanels';
import { MARKS, cropVersions, foundCrop, recordIssue, recordState, type FoundCrop } from '../cropRecords';

type Json = Record<string, any>;
const json = (value: unknown, status = 200, headers: HeadersInit = {}) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers },
});
const OPEN = { 'access-control-allow-origin': '*' };
const ABSENT = 'This character is not in the published collection.';

// A route's crop: the live crop it names, a redirect for an old or retired id, or a 404.
async function named(env: Env, ctx: ExecutionContext, url: URL, raw: string): Promise<FoundCrop | Response> {
  const param = await cropParam(env, raw, ctx);
  if (param && 'moved' in param) return movedPathResponse(url, raw, param.moved);
  const found = param && 'id' in param ? await foundCrop(env, param.id) : null;
  if (!found) throw new Problem(404, ABSENT);
  return found;
}

/** A book's dates on both axes with the claims they were resolved from, for the inspector. */
export const bookDatesQuery = () => `SELECT d.axis,d.kind,d.start,d.end,d.precision,d.qualifier,d.uncertain,d.text,d.label,d.status,d.calendar,
  d.conversion,d.source FROM books b JOIN book_dates d ON d.book=b.id WHERE b.key=?`;
// A book's date claims that stand: every `date_*` assertion its source has not retracted.
export const dateClaimsQuery = () => `SELECT a.id,a.predicate,a.value,a.tier,e.ref AS source,e.locator
  FROM assertions a LEFT JOIN assertion_evidence e ON e.assertion=a.id AND e.kind='source'
  WHERE a.subject=? AND substr(a.predicate,1,5)='date_'
    AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
  ORDER BY a.predicate,a.id`;
const parsed = (value: string | null) => { if (!value) return null; try { return JSON.parse(value) } catch { return null } };
export async function bookDates(env: Env, document: string | null): Promise<Json> {
  if (!document) return { dating: {} };
  const [axes, claims] = await env.ATLAS.batch([env.ATLAS.prepare(bookDatesQuery()).bind(document), env.ATLAS.prepare(dateClaimsQuery()).bind(document)]);
  const dating = Object.fromEntries((axes.results as Json[]).map(row => [row.axis, axisOf(row)]));
  const dates = (claims.results as Json[]).map(row => {
    const value = parsed(row.value) ?? {};
    return { id: row.id, kind: String(row.predicate).slice(5), of: value.of, text: value.text, start: value.start, end: value.end,
      precision: value.precision, qualifier: value.qualifier ?? null, uncertain: Boolean(value.uncertain), day: value.day ?? null,
      calendar: value.calendar, hutime: value.conversion?.query ?? null, note: value.note ?? null, tier: row.tier,
      source: row.source, locator: row.locator };
  });
  return { dating, ...(dates.length ? { dates } : {}) };
}

// The book a record is dated by: a collected crop's book, a corpus glyph's source document.
const documentFor = ({ row, record }: FoundCrop): string | null => row.kind === 1 ? record.source?.document_id ?? null : row.document;

/** A crop's record for its inspector, with its form, word, book's dates and the claims they rest on. */
export async function inspected(env: Env, found: FoundCrop): Promise<Json> {
  const id = found.row.id, document = documentFor(found);
  const [forms, words, dates] = await Promise.all([cropForms(env, [id]), cropWords(env, [id]), bookDates(env, document)]);
  const record = { ...found.record, form: forms.get(id) ?? null, word: words.get(id) ?? null };
  const honkoku = honkokuPage(record, document);
  return { ...record, ...(honkoku ? { honkoku_url: honkoku } : {}), ...dates };
}

/** Whether a suggestions request names the pixels the crop holds now: a corpus glyph by its source
 *  revision, a crop by its page hash, and a crop published without a hash by its image. */
export function samePixels(q: URLSearchParams, origin: string, data: Json): boolean {
  if (origin === 'corpus') return q.get('source_revision') === data.source_revision;
  return data.image_sha256 ? q.get('image_sha256') === data.image_sha256 : q.get('image') === data.image;
}
async function suggestions(env: Env, ctx: ExecutionContext, found: FoundCrop, q: URLSearchParams, context: boolean) {
  const { row, record } = found;
  // A client that read the crop before marks moved its revision still holds what is shown (`settled`).
  const revision = Number(q.get('revision') ?? NaN);
  if (!(Number.isSafeInteger(revision) && revision >= row.settled && revision <= row.revision) || !samePixels(q, row.kind === 1 ? 'corpus' : 'local', record)) throw new Problem(409, 'Character changed.');
  const votes = await cropVotes(env, row.id, row.cut, ctx);
  const engines = votes?.models == null ? null : await env.ATLAS.prepare(`SELECT key FROM codes WHERE field='models' AND code=?`)
    .bind(votes.models).first<{ key: string }>().then(found => found ? JSON.parse(found.key) as { name: string }[] : null);
  const restored = restoreVotes(engines, votes);
  return context ? restored.context : restored.visual;
}

// A citation of a crop at one of its evidence versions (`?v=`, as `versionToken` names it; the current one
// without), as CSL-JSON, BibTeX or Hayagriva (`?format=`). A token issued before the cutover names the
// version by the crop's old text id, and still finds it. It is read again once the catalogue version moves,
// and on each new day (in UTC), the date it was accessed.
const CITE_TTL = 3600;
const CITE_TYPES: Record<Format, string> = { csl: 'application/vnd.citationstyles.csl+json; charset=utf-8',
  bibtex: 'application/x-bibtex; charset=utf-8', hayagriva: 'application/yaml; charset=utf-8' };
async function cite(env: Env, ctx: ExecutionContext, url: URL, raw: string): Promise<Response> {
  let id: string;
  try { id = decodeURIComponent(raw) } catch { throw new Problem(404, 'Nothing is published at this address.') }
  const token = url.searchParams.get('v'), format = (url.searchParams.get('format') ?? 'csl') as Format;
  if (id.length > 512 || (token !== null && !/^[0-9a-z]{6}$/.test(token)) || !FORMATS.includes(format)) throw new Problem(422, 'Invalid citation address.');
  const crop = await named(env, ctx, url, raw);
  if (crop instanceof Response) {
    for (const [key, value] of Object.entries(OPEN)) crop.headers.set(key, value);
    return crop;
  }
  const name = publicId(crop.row.id);
  const now = new Date(), day: Day = [now.getUTCFullYear(), now.getUTCMonth() + 1, now.getUTCDate()];
  const key = new Request(`${url.origin}/atlas/cite/crop/${name}?v=${token ?? ''}&f=${format}&d=${day.join('-')}&c=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return new Response(cached.body, { headers: { ...Object.fromEntries(cached.headers), 'cache-control': 'no-cache' } });
  const { record, entry } = crop, document = documentFor(crop);
  const honkoku = honkokuPage(record, document);
  let version: string | null = record.crop_version ?? null;
  if (token !== null) {
    const versions = cropVersions(crop);
    version = citedVersion(name, token, versions.map(v => v.id));
    const old = version ? null : citedVersion(entry.id, token, versions.map(v => v.source));
    if (old) version = versions.find(v => v.source === old)!.id;
    if (!version) throw new Problem(404, 'This crop has no version by that name.');
  }
  const cited = cropEntry({ ...record, ...(honkoku ? { honkoku_url: honkoku } : {}) }, crop.row.kind === 1 ? 'corpus' : 'collection', version);
  const body = format === 'bibtex' ? bibtex(cited, url.origin, day) + '\n' : format === 'hayagriva' ? hayagriva(cited, url.origin, day)
    : JSON.stringify([csl(cited, url.origin, day)], null, 2);
  // The edge keeps a copy for the day it is dated; a browser asks again, so a citation is never dated a day late.
  const headers = { 'content-type': CITE_TYPES[format], 'x-content-type-options': 'nosniff', ...OPEN };
  ctx.waitUntil(caches.default.put(key, new Response(body, { headers: { ...headers, 'cache-control': `public, max-age=${CITE_TTL}` } })));
  return new Response(body, { headers: { ...headers, 'cache-control': 'no-cache' } });
}

// Collected crops as a line or a page lists them: each crop's row with the revision the old site gave it
// (`recordQuery`), its page's revision, and the issue it
// stands flagged for, its last event's (`recordQuery`). One primary-key lookup per crop, and for a flagged
// one the last entry of its `event_crop` range.
export const listedCropsQuery = () => `SELECT c.id,s.key AS state,c.revision-${MARKS('c')} AS revision,g.text AS char,pg.rev,r.x,r.y,r.w,r.h,
  CASE WHEN s.key='flagged' THEN (SELECT i.key FROM events e LEFT JOIN codes i ON i.field='issue' AND i.code=e.issue
    WHERE e.crop=c.id ORDER BY e.revision DESC LIMIT 1) END AS issue
  FROM json_each(?1) j JOIN crops c ON c.id=j.value AND c.kind=0 JOIN pages pg ON pg.id=c.id>>24
  LEFT JOIN codes s ON s.field='state' AND s.code=c.state LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN reviews r ON r.crop=c.id`;
type Listed = { id: number; state: string | null; revision: number; char: string | null; rev: number; x: number | null; y: number | null;
  w: number | null; h: number | null; issue: string | null };
/** Each listed crop with its bundle entry, by id; a crop not collected or not in its bundle is absent. */
async function listedCrops(env: Env, ids: number[]): Promise<Map<number, { row: Listed; entry: Json }>> {
  if (!ids.length) return new Map();
  const rows = (await env.ATLAS.prepare(listedCropsQuery()).bind(JSON.stringify(ids)).all<Listed>()).results;
  const pages = new Map(rows.map(row => [pageOf(row.id), row.rev]));
  const bundles = new Map(await Promise.all([...pages].map(async ([page, rev]) => [page, await bundle(env, page, rev)] as const)));
  return new Map(rows.flatMap(row => {
    const entry = bundles.get(pageOf(row.id))?.bySlot.get(slotOf(row.id));
    return entry && !entry.retired ? [[row.id, { row, entry }] as const] : [];
  }));
}
const boxOf = (row: Listed, entry: Json) => row.x !== null ? { x: row.x, y: row.y, w: row.w, h: row.h } : entry.box ?? null;

// A crop's neighbours on its line, in reading order: the pairs `runs` holds are followed forward by their
// first crop (`run_first`) and backward by their second (`run_second`), at most `LINE_REACH` steps each way,
// one row a step. The size test is kept off the forward step's index (`+`), or the planner would read every
// pair of the size; the backward step repeats `run_second`'s condition, so its index is used.
const LINE_REACH = 4;
export const lineQuery = () => `WITH RECURSIVE ahead(id,d) AS (SELECT ?1,0 UNION ALL
    SELECT p.second,a.d+1 FROM ahead a CROSS JOIN runs p ON p.first=a.id AND +p.size=2 WHERE a.d<${LINE_REACH}),
  behind(id,d) AS (SELECT ?1,0 UNION ALL
    SELECT p.first,b.d-1 FROM behind b CROSS JOIN runs p ON p.second=b.id AND p.size=2 WHERE b.d>-${LINE_REACH})
  SELECT d,id FROM ahead UNION ALL SELECT d,id FROM behind WHERE d<0`;
// What the inspector's line strip needs of each neighbour: the crop as the review path judges it (revision
// and pixels), the label it carries now, and what is painted in its place until it loads. A crop with no
// neighbours has an empty line.
export function lineItems(steps: { d: number; id: number }[], crops: Map<number, { row: Listed; entry: Json }>): Json[] {
  const seen = new Set<number>();
  const items = [...steps].sort((a, b) => a.d - b.d).flatMap(step => {
    const found = crops.get(step.id);
    if (!found || seen.has(step.id)) return [];
    seen.add(step.id);
    const { row, entry } = found;
    return [{ id: publicId(row.id), offset: step.d, label: row.char ?? entry.label ?? null, image: entry.image ?? null, revision: row.revision,
      image_sha256: entry.image_sha256 ?? null, state: recordState(row.state), issue: recordIssue(row.issue), tone: entry.tone ?? null, image_size: entry.image_size ?? null }];
  });
  return items.length > 1 ? items : [];
}
// The edge keeps a copy per catalogue version; a review or an undo makes a new key.
async function line(env: Env, ctx: ExecutionContext, url: URL, id: number, name: string) {
  const key = new Request(`${url.origin}${url.pathname}?v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(key);
  if (cached) return await cached.json() as Json;
  const steps = (await env.ATLAS_RUNS.prepare(lineQuery()).bind(id).all<{ d: number; id: number }>()).results;
  const body = { id: name, reach: LINE_REACH, items: lineItems(steps, await listedCrops(env, [...new Set(steps.map(s => s.id))])) };
  ctx.waitUntil(caches.default.put(key, Response.json(body, { headers: { 'cache-control': 'public, max-age=60' } })));
  return body;
}

// A page as the page view shows it: its published record with each of its collected crops as the site holds
// it now, label and box, so a review or a redrawn box shows on the page once the catalogue version moves. The
// record names its crops by source id in its order, which its bundle maps to slots; a crop retired or not on
// the page is left out. The photo is a request of the holder's IIIF image service, its longer side at most
// PAGE_IMAGE pixels, laid over the page pixels the boxes are given in; a page no service serves is shown as
// its boxes alone. A page is named by its key.
const PAGE_IMAGE = 3000;
export function pageView(record: Json, crops: { row: Listed; entry: Json }[]): Json {
  const { crops: _, service, page_number, document, title, ...rest } = record;
  return { ...rest, document_id: document, document_title: title, seq: page_number - 1, service: service ?? null,
    image_url: service ? `${service}/full/!${PAGE_IMAGE},${PAGE_IMAGE}/0/default.jpg` : null, drawable: false,
    units: crops.map(({ row, entry }) => ({ id: publicId(row.id), line_id: entry.line_id ?? null, box: boxOf(row, entry), character: row.char,
      code_point: null, image: entry.image ?? null, state: recordState(row.state), issue: recordIssue(row.issue), tone: entry.tone ?? null, image_size: entry.image_size ?? null })) };
}
async function sourcePage(env: Env, ctx: ExecutionContext, url: URL, key: string) {
  const cacheKey = new Request(`${url.origin}${url.pathname}?v=${encodeURIComponent(await catalogueVersion(env))}`);
  const cached = await caches.default.match(cacheKey);
  if (cached) return await cached.json() as Json;
  const page = await env.ATLAS.prepare('SELECT id,rev FROM pages WHERE key=?').bind(key).first<{ id: number; rev: number }>();
  const read = page ? await bundle(env, page.id, page.rev) : null;
  if (!page || !read?.page) throw new Problem(404, 'This page is not in the published collection.');
  const slots = new Map([...read.bySlot.values()].map(entry => [entry.id, entry.slot]));
  const ids = ((read.page.crops ?? []) as string[]).flatMap(text => slots.has(text) ? [page.id * 2 ** 24 + slots.get(text)!] : []);
  const crops = await listedCrops(env, ids);
  const body = pageView(read.page, ids.flatMap(id => crops.has(id) ? [crops.get(id)!] : []));
  ctx.waitUntil(caches.default.put(cacheKey, Response.json(body, { headers: { 'cache-control': 'public, max-age=60' } })));
  return body;
}

const segment = (raw: string, message: string) => { try { return decodeURIComponent(raw) } catch { throw new Problem(404, message) } };

/** The answer of a crop route, or null for a path that is none of them. */
export async function cropRoute(env: Env, ctx: ExecutionContext, url: URL): Promise<Response | null> {
  const path = url.pathname, q = url.searchParams;
  if (path === '/atlas/corpus/character') {
    const raw = q.get('id') ?? '', param = await cropQueryParam(env, raw, ctx);
    if (param && 'moved' in param) return movedQueryResponse(url, 'id', param.moved);
    const found = param && 'id' in param ? await foundCrop(env, param.id) : null;
    if (!found) throw new Problem(404, ABSENT);
    return json(await inspected(env, found));
  }
  const sample = path.match(/^\/layers\/visual-groups\/samples\/([^/]+)\/image$/);
  if (sample) {
    const found = await named(env, ctx, url, sample[1]);
    if (found instanceof Response) return found;
    if (!found.record.image) throw new Problem(404, 'Image not found.');
    return Response.redirect(new URL(found.record.image, url).href, 302);
  }
  const neighbours = path.match(/^\/atlas\/characters\/([^/]+)\/line$/);
  if (neighbours) {
    const raw = neighbours[1], text = segment(raw, ABSENT), param = await cropParam(env, raw, ctx);
    if (param && 'moved' in param) return movedPathResponse(url, raw, param.moved);
    // An id that names no live crop has an empty line, as one with no neighbours.
    const body = param && 'id' in param ? await line(env, ctx, url, param.id, text) : { id: text, reach: LINE_REACH, items: [] };
    return json(body, 200, { 'cache-control': 'no-cache' });
  }
  const shown = path.match(/^\/atlas\/pages\/([^/]+)$/);
  if (shown) return json(await sourcePage(env, ctx, url, segment(shown[1], 'This page is not in the published collection.')), 200, { 'cache-control': 'no-cache' });
  const cited = path.match(/^\/atlas\/cite\/crop\/([^/]+)$/);
  if (cited) return await cite(env, ctx, url, cited[1]);
  const versions = path.match(/^\/atlas\/characters\/([^/]+)\/versions$/);
  if (versions) {
    segment(versions[1], ABSENT);
    const found = await named(env, ctx, url, versions[1]);
    if (found instanceof Response) return found;
    const document = found.row.document;
    return json({ id: publicId(found.row.id), current: found.record.crop_version ?? null,
      versions: cropVersions(found).map(({ id, pixels, box, image, at }) => ({ id, pixels, box, document, image, at })) });
  }
  const character = path.match(/^\/atlas\/characters\/([^/]+)(\/suggestions(\/context)?)?$/);
  if (character) {
    segment(character[1], ABSENT);
    const found = await named(env, ctx, url, character[1]);
    if (found instanceof Response) return found;
    if (character[2]) return json(await suggestions(env, ctx, found, q, Boolean(character[3])));
    return json(await inspected(env, found));
  }
  return null;
}
