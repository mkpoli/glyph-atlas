// Listing items on the new storage layout: each crop's bundle record under its row (`cropItems`), with
// what a listing shows beside it, as the old `itemsFor` gave it: the form a claim names for the crop's
// current cut (`form`), the forms tool's cluster and decision for a corpus glyph (`formed`), and its
// book's dates on both axes (`dating`).
import { cropItems, cropOf } from './atlas3';
import { axisOf } from './dating';
import { formed } from './forms';

type Json = any;
const cp = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');

// Per crop, by key: its cut, its book, and its forms tool cluster and glyph decision.
export const overlayQuery = () => `SELECT c.id,c.cut,c.kind,b.id AS book,c.cluster,f.key AS cluster_key,f.form AS cluster_form,
  f.issue AS cluster_issue,f.character AS cluster_character,f.written_family AS cluster_family,f.decision AS cluster_decision,
  g.crop AS glyph,g.form AS glyph_form,g.issue AS glyph_issue,g.character AS glyph_character,g.written_family AS glyph_family
  FROM json_each(?) j JOIN crops c ON c.id=j.value JOIN books b ON b.id=c.book
  LEFT JOIN form_clusters f ON f.id=c.cluster LEFT JOIN form_glyphs g ON g.crop=c.id`;
// Each book's dates, by the `book_dates` key.
export const datesQuery = () => `SELECT d.* FROM json_each(?) j JOIN book_dates d ON d.book=j.value`;
// The form claims that hold on each crop's current cut: one `current_claim_crop` seek per crop.
export const formClaimsQuery = () => `SELECT c.crop,c.status,c.members,c.claims,c.supporting FROM json_each(?) j
  JOIN current_claims c ON c.crop=j.value->>0 AND c.predicate='has_form' AND c.cut=j.value->>1 WHERE c.scope='' AND c.slot=''`;

type Overlay = { id: number; cut: number; kind: number; book: number; cluster: number | null; cluster_key: string | null;
  cluster_form: string | null; cluster_issue: string | null; cluster_character: string | null; cluster_family: string | null;
  cluster_decision: string | null; glyph: number | null; glyph_form: string | null; glyph_issue: string | null;
  glyph_character: string | null; glyph_family: string | null };

// The forms tool's word on a corpus glyph, in the shape `formed` reads: its own glyph decision, else its
// cluster's, whose issue `mixed` gives the glyph none.
function formsRow(o: Overlay) {
  if (o.cluster === null && o.glyph === null) return null;
  const own = o.glyph !== null, clusterIssue = o.cluster_issue === 'mixed' ? null : o.cluster_issue;
  return { id: '', cluster: o.cluster_key ?? '', clustered: o.cluster === null ? 0 : 1, glyph_set: own ? 1 : 0,
    form: own ? o.glyph_form : o.cluster_form, cluster_form: o.cluster_form, issue: own ? o.glyph_issue : clusterIssue,
    issue_character: own ? o.glyph_character : o.cluster_character, written_family: own ? o.glyph_family : o.cluster_family };
}

// A slot's values: its current members, or, disputed, each standing claim's.
const valuesOf = (row: Json): Json[] => row.members.length ? row.members
  : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : [];

/** Listing items for crops by id, in the order asked; a crop `cropItems` leaves out is left out. */
export async function listingItems(env: Env, ids: number[]): Promise<Json[]> {
  if (!ids.length) return [];
  const [items, overlays] = await Promise.all([cropItems(env, ids),
    env.ATLAS.prepare(overlayQuery()).bind(JSON.stringify(ids)).all<Overlay>().then(r => new Map(r.results.map(o => [o.id, o])))]);
  const shown = items.map(item => overlays.get(cropOf(item.id)!)!);
  const books = [...new Set(shown.map(o => o.book))];
  const [dates, claims] = await env.ATLAS.batch([
    env.ATLAS.prepare(datesQuery()).bind(JSON.stringify(books)),
    env.ATLAS.prepare(formClaimsQuery()).bind(JSON.stringify(shown.map(o => [o.id, o.cut]))),
  ]) as D1Result<Json>[];
  const dating = new Map<number, Json>();
  for (const row of dates.results) dating.set(row.book, { ...dating.get(row.book), [row.axis]: axisOf(row) });
  const forms = new Map(claims.results.map(row => {
    const parsed = { ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) };
    // Form names live with the representations, which are not in the new databases yet.
    const values = valuesOf(parsed).map((m: Json) => m.object ? { form: m.object, scheme: null, text: null, confidence: m.confidence }
      : { state: m.value, confidence: m.confidence });
    const by = [...new Set((parsed.claims as Json[]).filter(c => parsed.status === 'disputed' ? c.standing
      : c.members.some((m: Json) => parsed.supporting.includes(m.assertion))).map(c => c.asserted_by))];
    return [row.crop as number, { status: parsed.status, values, supporting: parsed.supporting, by }];
  }));
  return items.map((item, i) => {
    const o = shown[i];
    const record = o.kind === 1 ? formed(item, formsRow(o), { codePoints: cp }) : item;
    return { ...record, form: forms.get(o.id) ?? null, dating: dating.get(o.book) ?? {} };
  });
}
