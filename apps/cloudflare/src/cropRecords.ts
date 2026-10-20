// A crop's record on glyph-atlas-v3, as the inspector reads it: the bundle entry (a collected crop) or the
// published record its entry points to (a corpus glyph), under the crop's row and what reviews and form
// decisions have set since. The publisher leaves out of a bundle entry the fields a row, a review or a form
// decision holds and those the Worker computes (scripts/atlas3/build.py `ROW_FIELDS`); this puts them back.
import { bundle, publicId, pageOf, slotOf, type Entry } from './atlas3';
import { readRecords, recordKey } from './records';
import { formed, type UnitForm } from './forms';
import { categoryOf, isGugyeol } from './category';

type Json = Record<string, any>;
export const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');

// The page rectangle a reviewer may redraw a local crop's box in, in page pixels: the context the
// inspector shows, which lies inside the page. A corpus glyph's box belongs to its source, so it has none.
export function redrawLimits(data: Json): { x: number; y: number; w: number; h: number } | null {
  const c = data?.context_box, s = data?.source_scale;
  if (!data?.context || !c || !data.crop_box || !Array.isArray(s) || !(s[0] > 0) || !(s[1] > 0)) return null;
  return { x: Math.floor(c.x / s[0]), y: Math.floor(c.y / s[1]), w: Math.ceil((c.x + c.w) / s[0]) - Math.floor(c.x / s[0]), h: Math.ceil((c.y + c.h) / s[1]) - Math.floor(c.y / s[1]) };
}
// A crop's evidence version: its id, its image checksum and its box in whole pixels; a box of anything
// else has none. A crop is named by its public id.
export function cropVersion(id: string, data: Json): string | null {
  const pixels = data.image_sha256 ?? data.source_revision ?? null;
  if (typeof pixels !== 'string') return null;
  const box = data.box;
  if (box == null) return `${id}@${pixels}@`;
  const values = ['x', 'y', 'w', 'h'].map(key => box[key]);
  return values.every(Number.isSafeInteger) ? `${id}@${pixels}@${values.join(',')}` : null;
}

// Entry fields that are the bundle's own bookkeeping, never the record's.
const INTERNAL = ['slot', 'id', 'image_place', 'context_image_place', 'record_place', 'cuts', 'retired', 'replaced_by'];
// A mark (`seen`, `hard`) is the review queue's: the record of a marked crop is still pending.
const MARK_STATES = new Set(['seen', 'hard']);
// A review saved before the issue was renamed names it `reading`; a record calls it `character`.
export const recordIssue = (issue: string | null) => issue === 'reading' ? 'character' : issue;
export const recordState = (state: string | null) => state === null || MARK_STATES.has(state) ? 'pending' : state;

export type RecordRow = { id: number; kind: number; book: number; state: string | null; revision: number; cut: number;
  category: string | null; char: string | null; head: string | null; production: string | null; rev: number; page_key: string;
  document: string; x: number | null; y: number | null; w: number | null; h: number | null; issue: string | null;
  reviewed: number; written: number; settled: number; cluster_key: string | null; glyph_set: number; form: string | null; form_issue: string | null;
  form_character: string | null; written_family: string | null };
// An event that only moved a mark: a round's seen or hard (kinds 3 and 4), or an undo (kind 5) of a round
// that took one away. The old site kept marks beside the crop and never moved its revision for them.
const MARKED = (e: string) => `(${e}.kind IN (3,4) OR (${e}.kind=5 AND coalesce((SELECT k.kind FROM events k WHERE k.id=${e}.reverses),3) IN (3,4)))`;
/** How many mark events follow a crop's last other event: its revision less these is the one the old site
 *  gave it (`settled`). */
export const MARKS = (c: string) => `(SELECT count(*) FROM events e WHERE e.crop=${c}.id AND ${MARKED('e')} AND e.revision>coalesce((SELECT max(f.revision)
    FROM events f WHERE f.crop=${c}.id AND NOT ${MARKED('f')}),-1))`;
// One crop's row with everything its record takes from D1, every join a key lookup and every event read a
// range of `event_crop`. Each event holds the crop's values after it, undos (`reverses`) included, so a
// flagged crop's issue is its last event's. A verdict (kind below 3: match, wrong, unsure, 0003) that no undo
// reverses gives the record its category; the latest such event that named a character (`named`) makes the
// crop's label its written character.
export const recordQuery = () => `SELECT c.id,c.kind,c.book,s.key AS state,c.revision,c.cut,k.key AS category,g.text AS char,
  h.text AS head,p.key AS production,pg.rev,pg.key AS page_key,b.key AS document,r.x,r.y,r.w,r.h,
  CASE WHEN s.key='flagged' THEN (SELECT i.key FROM events e LEFT JOIN codes i ON i.field='issue' AND i.code=e.issue
    WHERE e.crop=c.id ORDER BY e.revision DESC LIMIT 1) END AS issue,
  EXISTS(SELECT 1 FROM events e WHERE e.crop=c.id AND e.kind<3 AND NOT EXISTS(SELECT 1 FROM events u WHERE u.reverses=e.id)) AS reviewed,
  EXISTS(SELECT 1 FROM events e WHERE e.crop=c.id AND e.named IS NOT NULL AND NOT EXISTS(SELECT 1 FROM events u WHERE u.reverses=e.id)) AS written,
  c.revision-${MARKS('c')} AS settled,
  fc.key AS cluster_key,fg.crop IS NOT NULL AS glyph_set,iif(fg.crop IS NULL,fc.form,fg.form) AS form,
  iif(fg.crop IS NULL,nullif(fc.issue,'mixed'),fg.issue) AS form_issue,iif(fg.crop IS NULL,fc.character,fg.character) AS form_character,
  iif(fg.crop IS NULL,fc.written_family,fg.written_family) AS written_family
  FROM crops c JOIN pages pg ON pg.id=c.id>>24 JOIN books b ON b.id=c.book
  LEFT JOIN codes s ON s.field='state' AND s.code=c.state LEFT JOIN codes k ON k.field='category' AND k.code=c.category
  LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN graphemes h ON h.id=g.head LEFT JOIN productions p ON p.id=c.production
  LEFT JOIN reviews r ON r.crop=c.id LEFT JOIN form_glyphs fg ON fg.crop=c.id LEFT JOIN form_clusters fc ON fc.id=coalesce(fg.cluster,c.cluster)
  WHERE c.id=?1 AND c.kind<2`;

// A corpus glyph's forms tool row, in the shape `formed` reads: its own glyph decision, else its cluster's.
function formRow(row: RecordRow): UnitForm | null {
  if (row.cluster_key === null && !row.glyph_set) return null;
  return { id: publicId(row.id), cluster: row.cluster_key ?? '', clustered: row.cluster_key === null ? 0 : 1, form: row.form,
    glyph_set: row.glyph_set ? 1 : 0, cluster_form: null, issue: row.form_issue, issue_character: row.form_character,
    written_family: row.written_family };
}

const scriptOf = (written: string) => /\p{Script=Katakana}/u.test(written) ? 'katakana' : /\p{Script=Hiragana}/u.test(written) ? 'hiragana'
  : /\p{Script=Han}/u.test(written) ? 'han' : /\p{Script=Hangul}/u.test(written) ? 'hangul' : /\p{Script=Tangut}/u.test(written) ? 'tangut'
  : isGugyeol(written) ? 'gugyeol' : 'symbol';

/** The record of a crop from its row and the published fields (`base`: the bundle entry, or the corpus
 *  glyph's published record). `source_id` is the id the crop had before the cutover, which listings carry
 *  too (`cropItems`), so an address written with it (a citation's version token) is still read. */
export function cropRecord(row: RecordRow, base: Json, source: string): Json {
  const corpus = row.kind === 1;
  let record: Json = Object.fromEntries(Object.entries(base).filter(([key]) => !INTERNAL.includes(key)));
  if (corpus) record = formed(record, formRow(row), { codePoints });
  Object.assign(record, { id: publicId(row.id), source_id: source });
  if (!corpus) Object.assign(record, { label: row.char ?? record.label, production: row.production ?? 'unknown', page_id: row.page_key });
  Object.assign(record, { state: recordState(row.state), revision: row.settled });
  // A review keeps the crop's category on its record, and the issue it stands flagged for.
  if (row.reviewed) record.category = row.category ?? categoryOf(record.label ?? '');
  if (row.reviewed || row.issue !== null) record.issue = record.state !== 'flagged' ? null : recordIssue(row.issue);
  if (row.written && row.char) {
    const written = row.char;
    Object.assign(record, { label: written, char: written, code_point: codePoints(written), written_character: written,
      identity_status: 'assigned', identity_basis: 'human_review', script: scriptOf(written),
      grapheme: codePoints(row.head ?? written), visual_group: null, category: categoryOf(written) });
  }
  // A redrawn box is the crop's until the next publication cuts it; the image shown is still the old cut.
  if (row.x !== null) Object.assign(record, { box: { x: row.x, y: row.y, w: row.w, h: row.h }, box_pending: true });
  return { ...record, crop_version: cropVersion(record.id, record), crop_editable: !corpus && Boolean(redrawLimits(record)) };
}

export type FoundCrop = { row: RecordRow; entry: Entry; record: Json };
/** A crop's row, bundle entry and record, or null for an id with no collected crop or corpus glyph, or one
 *  whose published record is missing. A storage error fails the call. */
export async function foundCrop(env: Env, id: number): Promise<FoundCrop | null> {
  const row = await env.ATLAS.prepare(recordQuery()).bind(id).first<RecordRow>();
  if (!row) return null;
  const entry = (await bundle(env, pageOf(id), row.rev))?.bySlot.get(slotOf(id));
  if (!entry || entry.retired) return null;
  let base: Json | undefined = entry;
  if (entry.record_place) {
    const pointer = { object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] };
    base = (await readRecords(env, [pointer])).get(recordKey(pointer));
  }
  return base ? { row, entry, record: cropRecord(row, base, entry.id) } : null;
}

/** A crop's current evidence version as its record shows it, with its cut and the source id its claims'
 *  `subject` names; null for no crop or one with no image. */
export async function cropVersionNow(env: Env, crop: number): Promise<{ cut: number; version: string; text: string } | null> {
  const found = await foundCrop(env, crop);
  const version = found?.record.crop_version;
  return found && version ? { cut: found.row.cut, version, text: found.entry.id } : null;
}

/** Every evidence version of a crop, oldest first: its earlier cuts (the bundle entry's `cuts`), then the one
 *  it has now. Each is named as `cropVersion` names it, by public id; `source` names it by the crop's id
 *  before the cutover. `at` is when the old site recorded an earlier cut; the current cut has none. */
export function cropVersions(found: FoundCrop): { id: string; cut: number; pixels: string | null; box: string; image: string | null; at: string | null; source: string }[] {
  const { row, entry, record } = found, name = publicId(row.id);
  const listed = [...(entry.cuts ?? []) as Json[]].sort((a, b) => a.cut - b.cut).map(cut => {
    const pixels = cut.pixels ?? cut.image_sha256 ?? null;
    return { cut: cut.cut as number, pixels, box: cut.box, image: cut.image ?? null, at: cut.at ?? null };
  });
  if (record.crop_version) listed.push({ cut: row.cut, pixels: record.image_sha256 ?? record.source_revision ?? null, box: record.box ?? null, image: record.image ?? null, at: null });
  return listed.flatMap(v => {
    const id = cropVersion(name, { image_sha256: v.pixels, box: v.box });
    return id ? [{ id, cut: v.cut, pixels: v.pixels, box: v.box ? ['x', 'y', 'w', 'h'].map(k => v.box[k]).join(',') : '', image: v.image, at: v.at,
      source: cropVersion(entry.id, { image_sha256: v.pixels, box: v.box })! }] : [];
  });
}
