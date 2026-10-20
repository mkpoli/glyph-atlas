// Listing items on the new storage layout: each crop's bundle record under its row's values
// (`cropItems`), with what the site has said of it since. A corpus glyph shows the form the forms tool
// decided for it or its cluster, every crop the form a person named for its cut (`has_form`), and every
// crop its book's dating.
import { cropItems, publicId, type Entry } from './atlas3';
import { codePoints } from './atlas3Answers';
import { axisOf } from './dating';
import { formed, type UnitForm } from './forms';

type Json = any;

// A corpus glyph's forms-tool decision: its own glyph decision (`form_glyphs`) when it has one, else its
// cluster's (`crops.cluster`); a cluster issue 'mixed' gives its glyphs no issue. One primary-key read
// per glyph and per cluster.
export const corpusFormsQuery = () => `SELECT c.id,c.cluster IS NOT NULL AS clustered,g.crop IS NOT NULL AS glyph_set,k.key AS cluster,
  k.form AS cluster_form,g.form AS glyph_form,g.issue AS glyph_issue,g.character AS glyph_character,g.written_family AS glyph_family,
  k.issue AS cluster_issue,k.character AS cluster_character,k.written_family AS cluster_family
  FROM json_each(?) j JOIN crops c ON c.id=j.value LEFT JOIN form_glyphs g ON g.crop=c.id
  LEFT JOIN form_clusters k ON k.id=coalesce(c.cluster,g.cluster) WHERE c.cluster IS NOT NULL OR g.crop IS NOT NULL`;
type CorpusForm = { id: number; clustered: number; glyph_set: number; cluster: string | null; cluster_form: string | null;
  glyph_form: string | null; glyph_issue: string | null; glyph_character: string | null; glyph_family: string | null;
  cluster_issue: string | null; cluster_character: string | null; cluster_family: string | null };
const unitForm = (row: CorpusForm): UnitForm => {
  const own = Boolean(row.glyph_set);
  return { id: publicId(row.id), cluster: row.cluster ?? '', clustered: row.clustered, glyph_set: row.glyph_set,
    cluster_form: row.cluster_form, form: own ? row.glyph_form : row.cluster_form,
    issue: own ? row.glyph_issue : row.cluster_issue === 'mixed' ? null : row.cluster_issue,
    issue_character: own ? row.glyph_character : row.cluster_character,
    written_family: own ? row.glyph_family : row.cluster_family };
};

// The form claims that hold on each crop's current cut: one `current_claim_crop` probe per crop. The
// names of forms live with the representations, which the new layout does not hold yet.
export const cropFormsQuery = () => `SELECT c.crop,c.status,c.members,c.claims,c.supporting FROM json_each(?) j
  JOIN current_claims c ON c.crop=j.value AND c.predicate='has_form' JOIN crops k ON k.id=c.crop
  WHERE c.scope='' AND c.slot='' AND c.cut=k.cut`;
function formOf(row: { status: string; members: string; claims: string; supporting: string }): Json {
  const members: Json[] = JSON.parse(row.members), claims: Json[] = JSON.parse(row.claims), supporting: string[] = JSON.parse(row.supporting);
  // The values a slot holds: its current members, or, disputed, each standing claim's.
  const values = members.length ? members : row.status === 'disputed' ? claims.filter(c => c.standing).flatMap(c => c.members) : [];
  return { status: row.status,
    values: values.map(m => m.object ? { form: m.object, scheme: null, text: null, confidence: m.confidence } : { state: m.value, confidence: m.confidence }),
    supporting,
    by: [...new Set(claims.filter(c => row.status === 'disputed' ? c.standing : c.members.some((m: Json) => supporting.includes(m.assertion)))
      .map(c => c.asserted_by))] };
}

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

/** The listing items of `ids`, in that order, with their forms and dating. A crop `cropItems` leaves
 *  out is left out here. */
export async function listingItems(env: Env, ids: number[], given?: Map<number, Entry>): Promise<Json[]> {
  const items = await cropItems(env, ids, given);
  if (!items.length) return items;
  const crops = new Map(ids.map(id => [publicId(id), id]));
  const listed = items.map(item => crops.get(item.id)!);
  const corpus = items.filter(item => item.origin === 'corpus').map(item => crops.get(item.id)!);
  const [decided, claimed, dating] = await Promise.all([
    corpus.length ? env.ATLAS.prepare(corpusFormsQuery()).bind(JSON.stringify(corpus)).all<CorpusForm>().then(r => r.results) : [],
    env.ATLAS.prepare(cropFormsQuery()).bind(JSON.stringify(listed)).all<{ crop: number; status: string; members: string; claims: string; supporting: string }>(),
    datingOf(env, items.map(item => item.document)),
  ]);
  const forms = new Map(decided.map(row => [row.id, unitForm(row)]));
  const claims = new Map(claimed.results.map(row => [row.crop, formOf(row)]));
  return items.map((item, i) => {
    const id = listed[i], shown = item.origin === 'corpus' ? formed(item, forms.get(id) ?? null, { codePoints }) : item;
    return { ...shown, form: claims.get(id) ?? null, dating: dating.get(item.document) ?? {} };
  });
}

// The classifier's doubt about a crop's label, and the quiz's shape order: one primary-key probe each.
export const marksQuery = () => `SELECT j.value AS crop,s.p,s.reads_as,s.label,s.box,h.shape_order FROM json_each(?) j
  LEFT JOIN suspects s ON s.crop=j.value LEFT JOIN shapes h ON h.crop=j.value`;
export type Suspect = { p: number; reads_as: string | null; label: string; box: Json | null };
// A mark holds while the crop keeps the label and box the classifier was shown; a crop relabelled or
// re-cut since is no longer what it judged.
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
