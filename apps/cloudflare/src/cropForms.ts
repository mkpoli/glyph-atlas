// The form a crop is written in, as the 字形 picker sets it (docs/design/form-model.md, migration 0051).
// A value the reviewer picks or types names a representation (`representation.ts`) and that value's
// broad form, which the first such choice creates together with the claim that the representation
// names it; the crop then gets a `has_form` claim on the evidence version its reviewer saw. Clearing it
// retracts the reviewer's own claim. Each save is one D1 batch with the resolution of its slots.
import { anchoredForm, representationId, typed, type FormProblem } from './representation';
import { commitPlan, planAction, planClaim, savedSubmission, submissionId, type LedgerTools, type Plan } from './ledger';

type Json = Record<string, any>;
const PROBLEMS: Record<FormProblem | 'private', string> = {
  character: 'Write one character or an ideographic description sequence.',
  component: 'A description is built from ideographs, radicals, strokes and katakana letters.',
  missing: 'This description is missing a component.',
  extra: 'This description has more components than its operators take.',
  private: 'A private-use character names nothing without the namespace of its mapping.',
};

/** Set (or with `form` null, clear) `actor`'s form for one crop, and answer with the crop's form and
 *  `replaced`: the value of the actor's own claim the save took back, or null, so a client can put it back. */
export async function setForm(env: Env, input: Json, cropId: string, actor: string, tools: LedgerTools & { literal: (value: string) => string }) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: cropId, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: saved.subject, form: (await formsFor(env, [saved.subject])).get(saved.subject) ?? null, replaced: saved.replaced ?? null };
  // The value is checked, and a clear finds the reader's own claim, before anything is written: a
  // corpus glyph nothing has named gets its row only for a save that will land.
  // A form chosen as another member of the crop's grapheme renames the crop first; the claim names that
  // review's submission id, so the ranking counts the two as one decision.
  if (input.review != null && !(typeof input.review === 'string' && /^[0-9a-f-]{36}$/i.test(input.review))) tools.fail(422, 'Invalid review id.');
  const typedForm = input.form == null ? null : tools.text(input.form, 256, 'form');
  const representation = typedForm ? typed(tools.literal(typedForm)) : null;
  if (typeof representation === 'string') tools.fail(422, PROBLEMS[representation]);
  const own = await env.DB.prepare(`SELECT a.id, r.value AS text FROM assertions a LEFT JOIN forms f ON f.id=a.object LEFT JOIN representations r ON r.id=f.anchor
    WHERE a.subject=? AND a.predicate='has_form' AND a.scope='' AND a.slot='' AND (a.asserted_by=? OR a.asserted_by IN ${tools.owned(actor)})
    AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
    ORDER BY a.rowid DESC LIMIT 1`).bind(cropId, actor).first<{ id: string; text: string | null }>();
  if (!representation && !own) tools.fail(409, 'You have no form on this crop to clear.');
  const crop = await tools.crop(env, cropId, input.crop_version);
  if (!crop.version) tools.fail(409, 'This crop has no image to make a claim about.');
  if (input.crop_version !== crop.version) tools.fail(409, 'This crop was cut again. Reload it.');
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [] };
  if (representation) {
    const named = await representationId(representation), form = await anchoredForm(named);
    plan.statements.push(env.DB.prepare('INSERT OR IGNORE INTO representations(id,scheme,value,namespace,version) VALUES(?,?,?,?,?)')
      .bind(named, representation.scheme, representation.value, representation.namespace, representation.version));
    // The first choice of a value names its form: the form and the claim that the value names it,
    // under an id derived from the form, so two first choices made at once name it once.
    if (!await env.DB.prepare('SELECT 1 FROM forms WHERE id=?').bind(form).first()) {
      plan.statements.push(env.DB.prepare('INSERT OR IGNORE INTO forms(id,anchor,created_by,created_at) VALUES(?,?,?,?)').bind(form, named, actor, at),
        env.DB.prepare(`INSERT OR IGNORE INTO assertions(id,submission,subject,predicate,scope,slot,object,tier,asserted_by,asserted_at,method)
          VALUES(?,?,?,'represented_by','',?,?,'editorial',?,?,'form-picker')`).bind(await namingId(form), key, form, named, named, actor, at));
      plan.keys.push([form, 'represented_by', '', named]);
    }
    const claim = await planClaim(env, { key, actor, subject: crop.id, predicate: 'has_form', scope: '', at, method: 'form-picker',
      members: [{ object: form, value: null, confidence: null, confidence_scheme: null }], version: crop.version }, tools);
    plan.statements.push(...claim.statements); plan.keys.push(...claim.keys);
  } else {
    const cleared = await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, tools);
    plan.statements.push(...cleared.statements); plan.keys.push(...cleared.keys);
  }
  const replaced = own?.text ?? null;
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, subject: crop.id, replaced }, tools);
  return { id: crop.id, form: (await formsFor(env, [crop.id])).get(crop.id) ?? null, replaced };
}

// The id of the claim that names a form, in the shape of the site's other ids and the same for every
// first choice of its value.
async function namingId(form: string) {
  const hex = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('named\n' + form)))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  return `cf:${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

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

/** Each crop's form as a page shows it: the status of its slot, the forms and states it holds now (all
 *  the competing ones when people disagree), each form's name, and the claims it rests on. */
export async function formsFor(env: Env, ids: string[]): Promise<Map<string, Json>> {
  const found = new Map<string, Json>();
  const wanted = [...new Set(ids)].slice(0, PAGE_MAX);
  if (!wanted.length) return found;
  const rows = (await env.DB.prepare(cropFormsQuery()).bind(JSON.stringify(wanted)).all<Json>()).results;
  if (!rows.length) return found;
  const parsed: Json[] = rows.map(row => ({ ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) }));
  // The values a slot holds: its current members, or, disputed, each standing claim's.
  const valuesOf = (row: Json): Json[] => row.members.length ? row.members
    : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : [];
  const forms = [...new Set(parsed.flatMap(row => valuesOf(row).flatMap((m: Json) => m.object ? [m.object] : [])))];
  const names = new Map(forms.length ? (await env.DB.prepare(formNamesQuery()).bind(JSON.stringify(forms)).all<{ id: string; scheme: string; value: string }>())
    .results.map(r => [r.id, { scheme: r.scheme, text: r.value }]) : []);
  for (const row of parsed) {
    const values = valuesOf(row).map((m: Json) => m.object ? { form: m.object, ...(names.get(m.object) ?? { scheme: null, text: null }), confidence: m.confidence }
      : { state: m.value, confidence: m.confidence });
    // Who made the claims the slot holds now (each competing one's, when people disagree), so a reader
    // can be offered to clear their own.
    const by = [...new Set((row.claims as Json[]).filter(c => row.status === 'disputed' ? c.standing : c.members.some((m: Json) => row.supporting.includes(m.assertion)))
      .map(c => c.asserted_by))];
    found.set(row.subject, { status: row.status, values, supporting: row.supporting, by });
  }
  return found;
}

/** `items` with the form each holds, for a listing or an inspector. */
export async function withForms<T extends Json>(env: Env, items: T[]): Promise<(T & { form: Json | null })[]> {
  const forms = await formsFor(env, items.map(item => item.id));
  return items.map(item => ({ ...item, form: forms.get(item.id) ?? null }));
}
