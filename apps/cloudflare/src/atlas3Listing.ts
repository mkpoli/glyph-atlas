import { WRITTEN, writtenRecord } from './cropRecords';
import { cropForms } from './cropPanels';
// Listing items on the new storage layout: each crop's bundle record under its row's values
// (`cropItems`), with what the site has said of it since. A corpus glyph shows the form the forms tool
// decided for it or its cluster, every crop the form a person named for its cut (`has_form`), and every
// crop its book's dating.
import { codeNames, cropItems, publicId, type Entry } from './atlas3';
import { codePoints } from './atlas3Answers';
import { axisOf } from './dating';
import { formed, type UnitForm } from './forms';
import type { Timing } from './timing';

type Json = any;

// A corpus glyph's forms-tool decision: its `form_glyphs` row (its own glyph decision, or a cluster
// decision of an earlier clustering it carries) when it has one, else its cluster's (`crops.cluster`); a
// cluster issue 'mixed' gives its glyphs no issue. One primary-key read per glyph and per cluster.
export const corpusFormsQuery = () => `SELECT c.id,c.cluster IS NOT NULL AS clustered,g.crop IS NOT NULL AS held,g.decision IS NOT g.carried AS glyph_set,k.key AS cluster,
  k.form AS cluster_form,g.form AS glyph_form,g.issue AS glyph_issue,g.character AS glyph_character,g.written_family AS glyph_family,
  k.issue AS cluster_issue,k.character AS cluster_character,k.written_family AS cluster_family
  FROM json_each(?) j JOIN crops c ON c.id=j.value LEFT JOIN form_glyphs g ON g.crop=c.id
  LEFT JOIN form_clusters k ON k.id=coalesce(c.cluster,g.cluster) WHERE c.cluster IS NOT NULL OR g.crop IS NOT NULL`;
type CorpusForm = { id: number; clustered: number; held: number; glyph_set: number; cluster: string | null; cluster_form: string | null;
  glyph_form: string | null; glyph_issue: string | null; glyph_character: string | null; glyph_family: string | null;
  cluster_issue: string | null; cluster_character: string | null; cluster_family: string | null };
const unitForm = (row: CorpusForm): UnitForm => {
  const held = Boolean(row.held);
  return { id: publicId(row.id), cluster: row.cluster ?? '', clustered: row.clustered, glyph_set: row.glyph_set,
    cluster_form: row.cluster_form, form: held ? row.glyph_form : row.cluster_form,
    issue: held ? row.glyph_issue : row.cluster_issue === 'mixed' ? null : row.cluster_issue,
    issue_character: held ? row.glyph_character : row.cluster_character,
    written_family: held ? row.glyph_family : row.cluster_family };
};

// Each book's dating on both axes, by book key: one unique-key and one primary-key probe per book.
export const datingQuery = () => `SELECT b.key AS document,d.axis,d.kind,d.start,d.end,d.precision,d.qualifier,d.uncertain,
  d.text,d.label,d.status,d.calendar,d.conversion,d.source FROM json_each(?) j JOIN books b ON b.key=j.value
  JOIN book_dates d ON d.book=b.id`;
/** Each named book's axes, or none for a book no source dates. */
export async function datingOf(env: Env, documents: (string | null | undefined)[]): Promise<Map<string, Json>> {
  const wanted = [...new Set(documents.filter((d): d is string => Boolean(d)))], found = new Map<string, Json>();
  if (!wanted.length) return found;
  for (const row of (await env.ATLAS.prepare(datingQuery()).bind(JSON.stringify(wanted)).all<Json>()).results)
    found.set(row.document, { ...found.get(row.document), [row.axis]: axisOf(row) });
  return found;
}

// What reviews have set on a crop since its cut: a redrawn box (`reviews`), which the crop shows until the
// next publication cuts it, and the issue its last event flagged it with. One primary-key probe and one
// `event_crop` range per crop.
export const reviewedQuery = () => `SELECT j.value AS crop,r.x,r.y,r.w,r.h,g.text AS char,h.text AS head,${WRITTEN('c.id')} AS written,
  (SELECT coalesce(e.kept_issue,e.issue) FROM events e WHERE e.crop=j.value ORDER BY e.revision DESC LIMIT 1) AS issue
  FROM json_each(?) j JOIN crops c ON c.id=j.value LEFT JOIN reviews r ON r.crop=c.id
  LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN graphemes h ON h.id=g.head`;
type Reviewed = { crop: number; x: number | null; y: number | null; w: number | null; h: number | null; issue: number | null; char: string | null; head: string | null; written: number };
// A review saved before the issue was renamed names it `reading`; a record calls it `character`.
const issueName = (name: string | undefined) => name === 'reading' ? 'character' : name ?? null;

/** The listing items of `ids`, in that order, with what reviews set since the cut (a redrawn box, a
 *  flagged crop's issue), their forms and dating. A crop `cropItems` leaves out is left out here. */
export async function listingItems(env: Env, ids: number[], given?: Map<number, Entry>, timing?: Timing): Promise<Json[]> {
  const timed = <T>(name: string, work: Promise<T>) => timing ? timing.beside(name, work) : work;
  const items = await timed('crops', cropItems(env, ids, given));
  if (!items.length) return items;
  const crops = new Map(ids.map(id => [publicId(id), id]));
  const listed = items.map(item => crops.get(item.id)!);
  const corpus = items.filter(item => item.origin === 'corpus').map(item => crops.get(item.id)!);
  const [decided, claimed, dating, reviewed] = await timed('reviews', Promise.all([
    corpus.length ? env.ATLAS.prepare(corpusFormsQuery()).bind(JSON.stringify(corpus)).all<CorpusForm>().then(r => r.results) : [],
    cropForms(env, listed),
    datingOf(env, items.map(item => item.document)),
    env.ATLAS.prepare(reviewedQuery()).bind(JSON.stringify(listed)).all<Reviewed>(),
  ]));
  const forms = new Map(decided.map(row => [row.id, unitForm(row)]));
  const claims = claimed;
  const since = new Map(reviewed.results.map(row => [row.crop, row]));
  let names = await codeNames(env);
  if (reviewed.results.some(row => row.issue !== null && !names.has(`issue:${row.issue}`))) names = await codeNames(env, true);
  return items.map((item, i) => {
    const id = listed[i], shown = item.origin === 'corpus' ? formed(item, forms.get(id) ?? null, { codePoints }) : item;
    const r = since.get(id);
    return { ...(r?.written && r.char ? writtenRecord(shown, r.char, r.head) : shown),
      ...(r && r.x !== null ? { box: { x: r.x, y: r.y, w: r.w, h: r.h }, box_pending: true } : {}),
      issue: item.state === 'flagged' && r?.issue != null ? issueName(names.get(`issue:${r.issue}`)) : null,
      form: claims.get(id) ?? null, dating: dating.get(item.document) ?? {} };
  });
}

// The classifier's doubt about a crop's label, and the quiz's shape order: one primary-key probe each.
// The doubt is held against the crop as it shows now (`suspectOf`), never read from `crops.flags`.
export const marksQuery = () => `SELECT j.value AS crop,s.p,s.reads_as,s.label,s.box,h.shape_order FROM json_each(?) j
  LEFT JOIN suspects s ON s.crop=j.value LEFT JOIN shapes h ON h.crop=j.value`;
export type Suspect = { p: number; reads_as: string | null; label: string; box: Json | null };
// A mark holds while the crop keeps the label and box the classifier was shown; a crop relabelled,
// re-cut or redrawn since is no longer what it judged.
export function suspectOf(mark: Suspect | null | undefined, item: Json): Json | null {
  if (!mark || mark.label !== item.label) return null;
  const a = mark.box, b = item.box ?? null;
  const same = a === null || b === null ? a === b : ['x', 'y', 'w', 'h'].every(k => Math.abs(Number(a[k]) - Number(b[k])) < 1e-6);
  return same ? { p: mark.p, reads_as: mark.reads_as } : null;
}
/** Each crop's suspect mark (as `suspectOf` holds it against the item) and shape order. */
export async function marksOf(env: Env, ids: number[]): Promise<Map<number, { suspect: Suspect | null; shape_order: number | null }>> {
  if (!ids.length) return new Map();
  const rows = (await env.ATLAS.prepare(marksQuery()).bind(JSON.stringify(ids)).all<{ crop: number; p: number | null; reads_as: string | null;
    label: string | null; box: string | null; shape_order: number | null }>()).results;
  return new Map(rows.map(row => [row.crop, { shape_order: row.shape_order,
    suspect: row.label === null ? null : { p: row.p!, reads_as: row.reads_as, label: row.label, box: row.box === null ? null : JSON.parse(row.box) } }]));
}
