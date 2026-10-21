// The word a crop writes at its place in the text (decision 0004): the `writes_word` slot of the crop's
// current cut. `wordSlot` shapes a slot for every reader; the picker's writes are in `routes/claims.ts`.
import { slotAuthors, slotValues } from './cropForms';
import { PREDICATES } from './ledger';

type Json = Record<string, any>;
const spec = PREDICATES.writes_word;
export const formOf = (confidence: number | null, scheme: string | null) => scheme === 'form'
  ? Object.entries(spec.confidence!.form).find(([, value]) => value === confidence)?.[0] ?? null : null;

// The claims a slot rests on: its supporting ones, or, disputed, each standing claim.
export const slotBacking = (row: Json): string[] => row.status === 'disputed'
  ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members.map((m: Json) => m.assertion)) : row.supporting;
/** A crop's word as a page shows it, from its parsed `writes_word` slot, the words' names and the
 *  methods of the claims it rests on. */
export function wordSlot(row: Json, names: Map<string, { reading: string; class: string }>, methods: Map<string, string>): Json {
  const values = slotValues(row).map(m => ({ ...(m.object ? { word: m.object, ...(names.get(m.object) ?? { reading: null, class: null }) } : { gloss: m.value.gloss }),
    form: formOf(m.confidence, m.scheme) }));
  const held = new Set(slotBacking(row).map(id => methods.get(id)));
  return { status: row.status, values, methods: spec.methods!.filter(method => held.has(method)), supporting: row.supporting, by: slotAuthors(row) };
}
