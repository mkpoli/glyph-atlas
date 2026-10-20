// What the forms tool shares with the rest of the Worker: the tools its routes take (routes/forms.ts),
// what a decision may name, and a corpus record shown with the form a person named for it.
import { formProblem, isSequence } from './representation';
type Json = Record<string, any>;
export type FormTools = {
  fail: (status: number, message: string, extra?: Record<string, unknown>) => never;
  body: (request: Request) => Promise<Json>;
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  codePoints: (value: string) => string;
};
type UnitForm = { id: string; cluster: string; clustered?: number; form: string | null; glyph_set: number; cluster_form: string | null;
  issue: string | null; issue_character: string | null; written_family: string | null };

// A glyph decision names at most this many glyphs; the view sends larger selections in parts.
export const GLYPHS_PER_DECISION = 1000;

const basis = (row: { glyph_set: number; form: string | null; issue?: string | null }) =>
  row.glyph_set ? 'form_glyph' : row.form || row.issue ? 'form_cluster' : null;

/** Whether a form is written as a well-formed ideographic description sequence. */
export const isDescription = (value: string) => isSequence(value) && formProblem(value) === null;

// A CODH record as published, shown with the form a person has since named for it.
export async function withForm(env: Env, record: Json, tools: Pick<FormTools, 'codePoints'>): Promise<Json> {
  const row = await env.DB.prepare(`SELECT ${FORM_COLUMNS} FROM form_units WHERE id=?`).bind(record.id).first<UnitForm>();
  return formed(record, row, tools);
}
// The `form_units` columns `formed` reads, for a query that joins them to its own rows.
export const FORM_COLUMNS = 'id,cluster,clustered,form,glyph_set,cluster_form,issue,issue_character,written_family';
export type { UnitForm };
export function formed(record: Json, row: UnitForm | null, tools: Pick<FormTools, 'codePoints'>): Json {
  if (row?.clustered === 0) {
    record = { ...record };
    delete record.form_cluster;
  }
  const membership = row && row.clustered !== 0 ? { form_cluster: { id: row.cluster } } : {};
  if (!row || (!row.form && !row.issue && !row.glyph_set)) return { ...record, ...membership };
  const decided = { ...membership, form_decision: { form: row.form, basis: basis(row) } };
  // A glyph reported as another character shows that character; one only marked off its form shows none.
  const written = row.form ?? row.issue_character;
  if (!written) return { ...record, ...decided, written_character: null, identity_status: 'unassigned', identity_basis: basis(row) ?? 'form_glyph' };
  return { ...record, ...decided, written_character: written, label: written, char: written, code_point: tools.codePoints(written),
    identity_status: 'assigned', identity_basis: basis(row) ?? 'form_glyph', ...(row.written_family ? { grapheme: row.written_family } : {}) };
}
