// A crop's form and word panels on glyph-atlas-v3: its `has_form` and `writes_word` slots in the claim
// ledger that hold on the crop's current cut, with the names of the forms and words they hold. A crop is
// named by its integer id; a claim on an earlier cut is not live (decision record 0006).
import { PREDICATES } from './ledger';

type Json = Record<string, any>;
const spec = PREDICATES.writes_word;
// At most this many crops in one call; a listing page holds fewer.
const PAGE_MAX = 200;

// The slot of each crop on its current cut: one `current_claim_crop` probe per crop.
export const cropSlotsQuery = () => `SELECT c.crop,c.status,c.members,c.claims,c.supporting FROM json_each(?1) j
  JOIN crops k ON k.id=j.value JOIN current_claims c ON c.crop=k.id AND c.predicate=?2 AND c.cut=k.cut
  WHERE c.scope='' AND c.slot=''`;
export const formNamesQuery = () => `SELECT f.id,r.scheme,r.value FROM forms f JOIN representations r ON r.id=f.anchor
  WHERE f.id IN (SELECT value FROM json_each(?))`;
export const wordNamesQuery = () => 'SELECT id,reading,class FROM words WHERE id IN (SELECT value FROM json_each(?))';
export const wordMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';

async function slots(env: Env, ids: number[], predicate: string): Promise<Json[]> {
  const wanted = [...new Set(ids)].slice(0, PAGE_MAX);
  if (!wanted.length) return [];
  const rows = (await env.ATLAS.prepare(cropSlotsQuery()).bind(JSON.stringify(wanted), predicate).all<Json>()).results;
  return rows.map(row => ({ ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) }));
}
// The values a slot holds: its current members, or, disputed, each standing claim's.
const valuesOf = (row: Json): Json[] => row.members.length ? row.members
  : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : [];
// Who made the claims the slot holds now (each competing one's, when people disagree), so a reader can be
// offered to clear their own.
const byOf = (row: Json) => [...new Set((row.claims as Json[]).filter(c => row.status === 'disputed' ? c.standing
  : c.members.some((m: Json) => row.supporting.includes(m.assertion))).map(c => c.asserted_by))];

/** Each crop's form: the status of its slot, the forms and states it holds now, each form's name, and the
 *  claims it rests on. A crop with no live slot is absent. */
export async function cropForms(env: Env, ids: number[]): Promise<Map<number, Json>> {
  const found = new Map<number, Json>(), parsed = await slots(env, ids, 'has_form');
  if (!parsed.length) return found;
  const forms = [...new Set(parsed.flatMap(row => valuesOf(row).flatMap((m: Json) => m.object ? [m.object] : [])))];
  const names = new Map(forms.length ? (await env.ATLAS.prepare(formNamesQuery()).bind(JSON.stringify(forms)).all<{ id: string; scheme: string; value: string }>())
    .results.map(r => [r.id, { scheme: r.scheme, text: r.value }]) : []);
  for (const row of parsed) {
    const values = valuesOf(row).map((m: Json) => m.object ? { form: m.object, ...(names.get(m.object) ?? { scheme: null, text: null }), confidence: m.confidence }
      : { state: m.value, confidence: m.confidence });
    found.set(row.crop, { status: row.status, values, supporting: row.supporting, by: byOf(row) });
  }
  return found;
}

const formOf = (confidence: number | null, scheme: string | null) => scheme === 'form'
  ? Object.entries(spec.confidence!.form).find(([, value]) => value === confidence)?.[0] ?? null : null;

/** Each crop's word slot, including competing readings when disputed. A crop with no live slot is absent. */
export async function cropWords(env: Env, ids: number[]): Promise<Map<number, Json>> {
  const found = new Map<number, Json>(), parsed = await slots(env, ids, 'writes_word');
  if (!parsed.length) return found;
  const words = [...new Set(parsed.flatMap(row => valuesOf(row).flatMap(m => m.object ? [m.object] : [])))];
  // The claims a slot rests on: its supporting ones, or, disputed, each standing claim.
  const backing = (row: Json): string[] => row.status === 'disputed'
    ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members.map((m: Json) => m.assertion)) : row.supporting;
  const supporting = [...new Set(parsed.flatMap(backing))];
  const names = new Map(words.length ? (await env.ATLAS.prepare(wordNamesQuery()).bind(JSON.stringify(words)).all<{ id: string; reading: string; class: string }>())
    .results.map(r => [r.id, { reading: r.reading, class: r.class }]) : []);
  const methods = new Map(supporting.length ? (await env.ATLAS.prepare(wordMethodsQuery()).bind(JSON.stringify(supporting)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  for (const row of parsed) {
    const values = valuesOf(row).map(m => ({ ...(m.object ? { word: m.object, ...(names.get(m.object) ?? { reading: null, class: null }) } : { gloss: m.value.gloss }),
      form: formOf(m.confidence, m.scheme) }));
    const held = new Set(backing(row).map(id => methods.get(id)));
    found.set(row.crop, { status: row.status, values, methods: spec.methods!.filter(method => held.has(method)), supporting: row.supporting, by: byOf(row) });
  }
  return found;
}
