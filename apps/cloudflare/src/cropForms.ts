// The form a crop is written in, as the 字形 picker sets it (docs/design/form-model.md): the
// `has_form` slot of the crop's current cut, read from the old database for listings that still read
// it. `formSlot` shapes a slot for every reader; the picker's writes are in `routes/claims.ts`.
type Json = Record<string, any>;

// The form claims of a page of crops that hold on each crop's current version: one key of
// `current_claims` per crop, and the names of the forms they mention by `forms`' and
// `representations`' own keys.
export const cropFormsQuery = () => `SELECT c.subject,c.status,c.object,c.value,c.members,c.claims,c.supporting FROM current_claims c
  JOIN units u ON u.id=c.subject WHERE c.subject IN (SELECT value FROM json_each(?)) AND c.predicate='has_form' AND c.scope='' AND c.slot=''
  AND c.crop_version IS u.crop_version`;
export const formNamesQuery = () => `SELECT f.id,r.scheme,r.value FROM forms f JOIN representations r ON r.id=f.anchor
  WHERE f.id IN (SELECT value FROM json_each(?))`;
// At most this many crops in one call; a listing page holds fewer.
const PAGE_MAX = 200;

// The values a slot holds: its current members, or, disputed, each standing claim's.
export const slotValues = (row: Json): Json[] => row.members.length ? row.members
  : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : [];
// Who made the claims the slot holds now (each competing one's, when people disagree), so a reader can
// be offered to clear their own.
export const slotAuthors = (row: Json): string[] => [...new Set((row.claims as Json[])
  .filter(c => row.status === 'disputed' ? c.standing : c.members.some((m: Json) => row.supporting.includes(m.assertion))).map(c => c.asserted_by as string))];
/** A crop's form as a page shows it, from its parsed `has_form` slot and the names of its forms: the
 *  status of the slot, the forms and states it holds now, each form's name, and the claims it rests on. */
export function formSlot(row: Json, names: Map<string, { scheme: string; text: string }>): Json {
  const values = slotValues(row).map((m: Json) => m.object ? { form: m.object, ...(names.get(m.object) ?? { scheme: null, text: null }), confidence: m.confidence }
    : { state: m.value, confidence: m.confidence });
  return { status: row.status, values, supporting: row.supporting, by: slotAuthors(row) };
}

/** Each crop's form as a page shows it: the status of its slot, the forms and states it holds now (all
 *  the competing ones when people disagree), each form's name, and the claims it rests on. */
export async function formsFor(env: Env, ids: string[]): Promise<Map<string, Json>> {
  const found = new Map<string, Json>();
  const wanted = [...new Set(ids)].slice(0, PAGE_MAX);
  if (!wanted.length) return found;
  const rows = (await env.DB.prepare(cropFormsQuery()).bind(JSON.stringify(wanted)).all<Json>()).results;
  if (!rows.length) return found;
  const parsed: Json[] = rows.map(row => ({ ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) }));
  const forms = [...new Set(parsed.flatMap(row => slotValues(row).flatMap((m: Json) => m.object ? [m.object] : [])))];
  const names = new Map(forms.length ? (await env.DB.prepare(formNamesQuery()).bind(JSON.stringify(forms)).all<{ id: string; scheme: string; value: string }>())
    .results.map(r => [r.id, { scheme: r.scheme, text: r.value }]) : []);
  for (const row of parsed) found.set(row.subject, formSlot(row, names));
  return found;
}

/** `items` with the form each holds, for a listing or an inspector. */
export async function withForms<T extends Json>(env: Env, items: T[]): Promise<(T & { form: Json | null })[]> {
  const forms = await formsFor(env, items.map(item => item.id));
  return items.map(item => ({ ...item, form: forms.get(item.id) ?? null }));
}
