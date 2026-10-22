// The form a crop is written in, as the 字形 picker sets it (docs/design/form-model.md): the
// `has_form` slot of the crop's current cut. `formSlot` shapes a slot for every reader; the picker's
// writes are in `routes/claims.ts`.
type Json = Record<string, any>;

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
