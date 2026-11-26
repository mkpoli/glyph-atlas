// The claim ledger's routes on glyph-atlas-v3 (decision record 0006): claims and actions, a crop's
// form and reading as the pickers set them, the ledger's reads and export, and a book's dates with the
// claims they rest on. Crops are named by public id at this boundary and by integer id inside.
import { bundle, cropOf, pageOf, publicId, slotOf as cropSlot } from '../atlas3';
import { aliasCrop } from '../atlas3Aliases';
import { characterIds, structureSource } from '../atlas3Reference';
import { StaleCrop, cropQueryParam, movedQueryResponse, staleResponse, writtenCropField } from '../atlas3Crops';
import { formSlot } from '../cropForms';
import { cropGraphemes, cropStructures, structureLabelsQuery, cropWords } from '../cropPanels';
import { cropReadings, READING_PREDICATES } from '../cropReadings';
import { slotBacking, wordSlot } from '../cropWords';
import { axisOf } from '../dating';
import { actOnClaim, checkClaims, claimsOf, commitPlan, type CropNow, effectiveCut, graphemeKey, headKeys, keyHeads, keyText, ledgerPage, type LedgerTools, type Member,
  ownedActors, planAction, planClaim, planSubjectClaim, PREDICATES, type Plan, resolveSlots, restsOn, retractOwn, RUN_MAX, sameVersion, savedSubmission, SQL_UUID, submissionId, withCrop, writeClaim } from '../ledger';
import { canonical, literal } from '../input';
import { readRecords, recordKey } from '../records';
import { refileRuns } from './runs';
import { FORM_TEXT_LONGEST, anchoredForm, representationId, structureKey, typed, written, type FormProblem, type Representation } from '../representation';

type Json = Record<string, any>;
export type ClaimTools = {
  fail: (status: number, message: string, extra?: Json) => never;
  body: (request: Request) => Promise<Json>;
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  canonical: (value: unknown) => string;
  literal: (value: string) => string;
  integer: (q: URLSearchParams, key: string, fallback: number, max?: number) => number;
};

// A crop's row, its page's revision and a redrawn box waiting for the next publication: primary-key
// lookups of `crops`, `pages` and `reviews`. A candidate takes no claim.
export const cropNowQuery = () => `SELECT c.cut,c.kind,p.rev,r.x,r.y,r.w,r.h FROM crops c JOIN pages p ON p.id=c.id>>24
  LEFT JOIN reviews r ON r.crop=c.id WHERE c.id=? AND c.kind<2`;
/** A crop's effective cut (the next one while a redrawn box waits, `effectiveCut`), its evidence
 *  version as its record shows it (`<public id>@<pixels>@<x,y,w,h>`, with the redrawn box), its text id
 *  and its kind; null for a crop with no row, no entry, or a candidate. */
export async function cropNow(env: Env, id: number): Promise<CropNow | null> {
  const row = await env.ATLAS.prepare(cropNowQuery()).bind(id).first<{ cut: number; kind: number; rev: number; x: number | null; y: number | null; w: number | null; h: number | null }>();
  if (!row) return null;
  const entry = (await bundle(env, pageOf(id), row.rev))?.bySlot.get(cropSlot(id));
  if (!entry || entry.retired) return null;
  const pointer = entry.record_place ? { object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] } : null;
  const record = pointer ? (await readRecords(env, [pointer])).get(recordKey(pointer)) : entry;
  if (!record) return null;
  const box = row.x !== null ? { x: row.x, y: row.y, w: row.w, h: row.h } : record.box;
  const pixels = record.image_sha256 ?? record.source_revision ?? null;
  const boxed = (box: Json | null | undefined) => {
    const values = box == null ? null : ['x', 'y', 'w', 'h'].map(key => box[key]);
    return values === null ? '@' : values.every(Number.isSafeInteger) ? '@' + values.join(',') : null;
  };
  const end = boxed(box), shown = typeof pixels === 'string' ? '@' + pixels : null;
  const images = shown === null ? null : [shown, ...((entry.encodings ?? []) as Json[]).flatMap(e => typeof e.pixels === 'string' ? ['@' + e.pixels] : [])];
  const version = shown === null || end === null ? null : publicId(id) + shown + end;
  return { id, cut: row.cut + (row.x !== null ? 1 : 0), version, text: entry.id, kind: row.kind, line: record.text ?? null,
    published: { cut: row.cut, pixels: images, box: shown === null ? null : boxed(record.box) } };
}

const NOT_HERE = 'This character is not in the published collection.';
/** The crop a write names, by public id or, from a page opened before the cutover, by old text id;
 *  a retired crop answers with the crop that replaced it, as the old site did. */
async function namedCrop(env: Env, named: unknown, fail: ClaimTools['fail'], ctx?: ExecutionContext): Promise<CropNow> {
  const id = await writtenCropField(env, named, ctx);
  if (id === null) fail(404, NOT_HERE);
  const crop = await cropNow(env, id!);
  if (crop) return crop;
  const standing = await cropQueryParam(env, publicId(id!), ctx);
  if (standing && 'moved' in standing) fail(404, 'This crop was replaced.', { replaced_by: publicId(standing.moved) });
  return fail(404, NOT_HERE);
}

const ledgerTools = (env: Env, tools: ClaimTools, ctx?: ExecutionContext, read = new Map<number, CropNow>()): LedgerTools => ({ fail: tools.fail, text: tools.text, canonical: tools.canonical,
  owned: ownedActors, alias: text => aliasCrop(env, text, ctx),
  crop: async (env, named) => { const crop = await namedCrop(env, named, tools.fail, ctx); read.set(crop.id, crop); return crop },
  now: async (id, afresh) => !afresh && read.get(id) || await cropNow(env, id),
  // A claim is saved in glyph-atlas-v3 and its runs filed in glyph-atlas-v3-runs, which cannot share a
  // transaction: a filing that fails is logged and the claim answers as saved, and the publisher files
  // the crop's runs from the claims as they stand once it next publishes.
  refile: crop => filedRuns(env, crop) });
/** Files a crop's runs from the claims as they stand; a failure is logged. */
export const filedRuns = (env: Env, crop: number) => refileRuns(env, crop)
  .catch(error => console.log(JSON.stringify({ event: 'refile_failed', crop, message: String(error) })));

// A crop's slot that holds on its effective cut: one key of `current_claims` per crop, by `current_claim_crop`.
export const cropSlotsQuery = (predicate: 'has_form' | 'writes_word') => `SELECT c.crop,c.status,c.object,c.value,c.members,c.claims,c.supporting
  FROM json_each(?) j JOIN current_claims c ON c.crop=j.value AND c.predicate='${predicate}'
  WHERE c.scope='' AND c.slot='' AND c.cut=${effectiveCut('c.crop')}`;
export const formNamesQuery = () => `SELECT f.id,r.scheme,r.value FROM forms f JOIN representations r ON r.id=f.anchor
  WHERE f.id IN (SELECT value FROM json_each(?))`;
export const wordNamesQuery = () => 'SELECT id,reading,class FROM words WHERE id IN (SELECT value FROM json_each(?))';
export const wordMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';
async function slots(env: Env, crop: number, predicate: 'has_form' | 'writes_word') {
  const row = await env.ATLAS.prepare(cropSlotsQuery(predicate)).bind(JSON.stringify([crop])).first<Json>();
  return row && { ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) };
}
const objects = (row: Json) => [...new Set((row.members.length ? row.members
  : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : []).flatMap((m: Json) => m.object ? [m.object] : []))];
/** A crop's form as its inspector shows it, or null. */
export async function formOfCrop(env: Env, crop: number): Promise<Json | null> {
  const row = await slots(env, crop, 'has_form');
  if (!row) return null;
  const forms = objects(row);
  const names = new Map(forms.length ? (await env.ATLAS.prepare(formNamesQuery()).bind(JSON.stringify(forms)).all<{ id: string; scheme: string; value: string }>())
    .results.map(r => [r.id, { scheme: r.scheme, text: r.value }]) : []);
  return formSlot(row, names);
}
/** A crop's word as its inspector shows it, or null. */
export async function wordOfCrop(env: Env, crop: number): Promise<Json | null> {
  const row = await slots(env, crop, 'writes_word');
  if (!row) return null;
  const words = objects(row), backing = slotBacking(row);
  const names = new Map(words.length ? (await env.ATLAS.prepare(wordNamesQuery()).bind(JSON.stringify(words)).all<{ id: string; reading: string; class: string }>())
    .results.map(r => [r.id, { reading: r.reading, class: r.class }]) : []);
  const methods = new Map(backing.length ? (await env.ATLAS.prepare(wordMethodsQuery()).bind(JSON.stringify(backing)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  return wordSlot(row, names, methods);
}

const PROBLEMS: Record<FormProblem | 'private', string> = {
  character: 'Write one character or an ideographic description sequence.',
  component: 'A description is built from ideographs, radicals, strokes and katakana letters.',
  missing: 'This description is missing a component.',
  extra: 'This description has more components than its operators take.',
  private: 'A private-use character names nothing without the namespace of its mapping.',
};
// The reader's own latest live claim in a crop's single-valued slot: one range of `assertion_crop`.
export const ownClaimQuery = (predicate: 'has_form' | 'has_grapheme' | 'built_as', owned: string) => `SELECT a.id,a.object,a.method,a.confidence,a.confidence_scheme,
  ${predicate === 'has_form' ? '(SELECT r.value FROM forms f JOIN representations r ON r.id=f.anchor WHERE f.id=a.object)' : 'NULL'} AS text
  FROM assertions a WHERE a.crop=? AND a.predicate='${predicate}' AND a.scope='' AND a.slot='' AND (a.asserted_by=? OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.rowid DESC LIMIT 1`;
type Own = { id: string; object: string | null; method: string; confidence: number | null; confidence_scheme: string | null; text: string | null };

/** Set (or with `form` null, clear) `actor`'s form for one crop, and answer with the crop's form and
 *  `replaced`: the value of the actor's own claim the save took back, or null, so a client can put it back.
 *  A value the reviewer picks or types names a representation and that value's broad form, which the
 *  first such choice creates together with the claim that the representation names it. */
export async function setForm(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) {
    const { crop } = await withCrop(env, saved, ledger.alias);
    if (crop == null) tools.fail(404, NOT_HERE);
    return { id: publicId(crop), form: await formOfCrop(env, crop), replaced: saved.replaced ?? null };
  }
  const typedForm = input.form == null ? null : tools.text(input.form, FORM_TEXT_LONGEST, 'form');
  const representation = typedForm ? typed(tools.literal(typedForm)) : null;
  if (typeof representation === 'string') tools.fail(422, PROBLEMS[representation]);
  const crop = await ledger.crop(env, named);
  const own = await env.ATLAS.prepare(ownClaimQuery('has_form', ownedActors(actor))).bind(crop.id, actor).first<Own>();
  if (!representation && !own) tools.fail(409, 'You have no form on this crop to clear.');
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [] };
  await planForm(env, { key, actor, crop, representation, own, at }, plan, new Set(), ledger);
  const replaced = own?.text ?? null;
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, cut: crop.cut, replaced }, ledger);
  return { id: publicId(crop.id), form: await formOfCrop(env, crop.id), replaced };
}

// The rows that name a value's broad form: its representation, and with the first choice of the value
// the form and the claim that the value names it, under an id derived from the form, so two first
// choices made at once name it once. Answers the form's id.
async function planNaming(env: Env, naming: { key: string; actor: string; representation: Representation; at: string }, plan: Plan) {
  const { key, actor, representation, at } = naming;
  const rep = await representationId(representation), form = await anchoredForm(rep);
  plan.statements.push(env.ATLAS.prepare('INSERT OR IGNORE INTO representations(id,scheme,value,namespace,version) VALUES(?,?,?,?,?)')
    .bind(rep, representation.scheme, representation.value, representation.namespace, representation.version));
  if (!await env.ATLAS.prepare('SELECT 1 FROM forms WHERE id=?').bind(form).first()) {
    plan.statements.push(env.ATLAS.prepare('INSERT OR IGNORE INTO forms(id,anchor,created_by,created_at) VALUES(?,?,?,?)').bind(form, rep, actor, at),
      env.ATLAS.prepare(`INSERT OR IGNORE INTO assertions(id,submission,subject,predicate,scope,slot,object,tier,asserted_by,asserted_at,method)
        VALUES(?,?,?,'represented_by','',?,?,'observed',?,?,'form-picker')`).bind(await namingId(form), key, form, rep, rep, actor, at));
    plan.keys.push([form, 'represented_by', '', rep]);
  }
  return form;
}

// The rows of one form pick: the reader's claim of `representation` on `crop`, or with none, the
// retraction of their own claim `own`. A value the reviewer picks or types names a representation and
// that value's broad form, which the first such choice creates together with the claim that the
// representation names it; `named` holds the forms a batch has already created, so it names each once.
async function planForm(env: Env, pick: { key: string; actor: string; crop: CropNow; representation: Representation | null; own: Own | null; at: string },
  plan: Plan, named: Set<string>, ledger: LedgerTools) {
  const { key, actor, crop, representation, own, at } = pick;
  const add = (part: { statements: D1PreparedStatement[]; keys: Plan['keys'] }) => { plan.statements.push(...part.statements); plan.keys.push(...part.keys) };
  plan.crops = [...plan.crops ?? [], crop.id];
  if (!representation) return add(await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger));
  const form = await anchoredForm(await representationId(representation));
  if (!named.has(form)) { named.add(form); await planNaming(env, { key, actor, representation, at }, plan); }
  add(await planClaim(env, { key, actor, crop, predicate: 'has_form', scope: '', at, method: 'form-picker',
    members: [{ object: form, value: null, confidence: null, confidence_scheme: null }] }, ledger));
}

/** The rows of a form claimed by the review that names its crop's character, for that review's own batch
 *  (routes/review.ts), and the ledger's row of the submission. The form is the character's: a character is
 *  itself and a figure its base, while a description names no character. The claim rests on the crop the
 *  reviewer saw (`seen`, its evidence version), or on the cut its new box makes when the review redraws it,
 *  so its slot is resolved after the review has written its box. */
export async function planReviewForm(env: Env, review: { key: string; actor: string; signature: string; at: string; id: number; character: string;
  form: string; seen: unknown; cut: number; box?: { x: number; y: number; w: number; h: number } }, fail: ClaimTools['fail']): Promise<D1PreparedStatement[]> {
  const { key, actor, signature, at, id, character, form, seen, cut, box } = review;
  const representation = typed(literal(form));
  if (typeof representation === 'string') return fail(422, PROBLEMS[representation]);
  const written = representation.scheme === 'ids' ? character : representation.scheme === 'ivs' ? [...representation.value][0] : representation.value;
  if (written !== character) fail(422, 'This form is another character’s.');
  const shown = await cropNow(env, id) ?? fail(404, NOT_HERE);
  sameVersion(shown, seen, fail);
  const crop = box ? { ...shown, cut: cut + 1, version: shown.version!.replace(/[^@]*$/, [box.x, box.y, box.w, box.h].join(',')) } : shown;
  const ledger = { fail, canonical, owned: ownedActors } as LedgerTools;
  const plan: Plan = { statements: [], keys: [] };
  await planForm(env, { key, actor, crop, representation, own: null, at }, plan, new Set(), ledger);
  return [...plan.statements, ...resolveSlots(env, plan.keys, [crop]), env.ATLAS.prepare('INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)')
    .bind(key, actor, signature, JSON.stringify({ submission: key, crop: crop.id, cut: crop.cut }), at)];
}

// The form claims a review's submission wrote on its crops that nobody has taken back, each with the claims of the
// same reader it replaced: the submission's ledger row, then each crop's forms by `assertion_crop`, then their actions by theirs.
export const reviewFormsQuery = () => `SELECT a.id,a.subject,a.crop,(SELECT json_group_array(json_object('id',o.id,'set',o.alternative_set)) FROM assertions o
    JOIN assertion_actions x ON x.assertion=o.id AND x.action='retract' WHERE o.crop=a.crop AND o.predicate='has_form' AND x.submission=?1) AS replaced
  FROM ledger_submissions l CROSS JOIN json_each(?2) j CROSS JOIN assertions a ON a.crop=j.value AND a.predicate='has_form'
  WHERE l.id=?1 AND a.submission=?1 AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`;
/** The rows that take back the form a review's submission claimed and put back the reader's own claim it
 *  replaced, for the batch that undoes the review (`by`, the reader or an admin). Rows of the ledger are
 *  never changed, so a claim comes back as a new row of the submission that made it, naming the row it
 *  restores among its premises and carrying the verdicts on it. A claim its reader has since replaced or
 *  taken back stays as they left it; nobody else's claim is touched, and the form's own rows stay, which
 *  other crops may name. */
export async function planFormUndo(env: Env, undone: { key: string; by: string; crops: number[]; at: string }): Promise<D1PreparedStatement[]> {
  const { key, by, crops, at } = undone;
  const claims = (await env.ATLAS.prepare(reviewFormsQuery()).bind(key, JSON.stringify(crops)).all<{ id: string; subject: string; crop: number; replaced: string }>()).results;
  if (!claims.length) return [];
  const shown = await Promise.all([...new Set(claims.map(claim => claim.crop))].map(crop => cropNow(env, crop)));
  const undo = key + ':undo', standing = (claim: string) => `NOT EXISTS(SELECT 1 FROM assertion_actions x WHERE x.assertion=${claim} AND x.action='retract')`;
  const statements = claims.flatMap(claim => {
    const sets = new Map<string, string>(), again = (set: string | null) => set && (sets.get(set) ?? sets.set(set, 'cf:' + crypto.randomUUID()).get(set)!);
    return [...(JSON.parse(claim.replaced) as { id: string; set: string | null }[]).flatMap(own => {
      const id = 'cf:' + crypto.randomUUID(), written = 'EXISTS(SELECT 1 FROM assertions WHERE id=?1)';
      return [env.ATLAS.prepare(`INSERT INTO assertions(id,submission,subject,crop,cut,predicate,scope,slot,object,value,alternative_set,tier,asserted_by,asserted_at,confidence,confidence_scheme,method,run,legacy)
          SELECT ?1,submission,subject,crop,cut,predicate,scope,slot,object,value,?2,tier,asserted_by,?3,confidence,confidence_scheme,method,run,legacy FROM assertions WHERE id=?4 AND ${standing('?5')}`)
          .bind(id, again(own.set), at, own.id, claim.id),
        env.ATLAS.prepare(`INSERT INTO assertion_evidence(assertion,kind,ref,crop,cut,locator) SELECT ?1,kind,ref,crop,cut,locator FROM assertion_evidence WHERE assertion=?2 AND ${written}`).bind(id, own.id),
        env.ATLAS.prepare(`INSERT INTO assertion_premises(assertion,premise,role) SELECT ?1,coalesce((SELECT premise FROM assertion_premises WHERE assertion=?2 AND role='restores'),?2),'restores' WHERE ${written}`).bind(id, own.id),
        env.ATLAS.prepare(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason)
          SELECT 'cf:'||${SQL_UUID},submission,?1,action,actor,at,reason FROM assertion_actions WHERE assertion=?2 AND action<>'retract' AND ${written} ORDER BY rowid`).bind(id, own.id)];
    }), env.ATLAS.prepare(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) SELECT ?1,?2,?3,'retract',?4,?5,'undone' WHERE ${standing('?3')}`)
      .bind('cf:' + crypto.randomUUID(), undo, claim.id, by, at)];
  });
  return [...statements, ...resolveSlots(env, [...new Set(claims.map(claim => claim.subject))].map(subject => [subject, 'has_form', '', '']), shown),
    env.ATLAS.prepare('INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)')
      .bind(undo, by, JSON.stringify({ undo: key }), JSON.stringify({ submission: undo }), at)];
}

// A selection marked as one form is a batch of picks, one per crop: each names the evidence version the
// reader saw and the value to claim, or null to take the reader's own claim back, which is how a batch
// is undone. One address sends a batch as it sends one pick; a batch is saved or refused whole.
export const FORM_BATCH_MAX = 48;
/** Set (or clear) `actor`'s form on each crop of `input.crops`, and answer with each crop's form and
 *  `replaced`, as `setForm` does for one. A crop with nothing to clear is left out of the answer. */
export async function setForms(env: Env, input: Json, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ forms: input });
  const answer = async (picked: { crop: number; replaced: string | null }[]) => ({ id,
    crops: await Promise.all(picked.map(async ({ crop, replaced }) => ({ id: publicId(crop), form: await formOfCrop(env, crop), replaced }))) });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return answer(saved.picked ?? []);
  const raw = input.crops;
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > FORM_BATCH_MAX) tools.fail(422, `A batch names 1–${FORM_BATCH_MAX} crops.`);
  const representations = raw.map((entry: Json) => {
    const typedForm = entry?.form == null ? null : tools.text(entry.form, FORM_TEXT_LONGEST, 'form');
    const representation = typedForm ? typed(tools.literal(typedForm)) : null;
    if (typeof representation === 'string') tools.fail(422, PROBLEMS[representation]);
    return representation as Representation | null;
  });
  const crops = await Promise.all(raw.map((entry: Json) => ledger.crop(env, entry?.id)));
  if (new Set(crops.map(crop => crop.id)).size !== crops.length) tools.fail(422, 'A batch names each crop once.');
  const recut = crops.filter((crop, i) => !crop.version || raw[i].crop_version !== crop.version);
  if (recut.length) tools.fail(409, 'Some of these crops were cut again. Reload them.', { targets: recut.map(crop => ({ id: publicId(crop.id) })) });
  const owns = await Promise.all(crops.map(crop => env.ATLAS.prepare(ownClaimQuery('has_form', ownedActors(actor))).bind(crop.id, actor).first<Own>()));
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [] }, named = new Set<string>(), picked: { crop: number; replaced: string | null }[] = [];
  for (const [i, crop] of crops.entries()) {
    if (!representations[i] && !owns[i]) continue;
    await planForm(env, { key, actor, crop, representation: representations[i], own: owns[i], at }, plan, named, ledger);
    picked.push({ crop: crop.id, replaced: owns[i]?.text ?? null });
  }
  // The response saved is the answer: a retry that landed first saved what it replaced.
  const committed = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, picked }, ledger);
  return answer(committed.picked ?? []);
}

// The id of the claim that names a form, in the shape of the site's other ids and the same for every
// first choice of its value.
async function namingId(form: string) {
  const hex = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('named\n' + form)))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  return `cf:${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/** The grapheme a new claim names: the head its key is filed under, so a claim made on a page read, or
 *  kept on a device, before its key joined another family is the head's. 422 for a character the site
 *  does not hold. */
async function claimedHead(env: Env, key: unknown, tools: ClaimTools): Promise<string> {
  if (!graphemeKey(key)) return tools.fail(422, 'Name a grapheme by its code points.');
  const head = (await keyHeads(env, [key])).get(key)!;
  return (await headKeys(env, [head])).length ? head : tools.fail(422, 'No such character.');
}
/** Set the reader's grapheme for one crop, named by its key (`U+5F8C`), or take back their own claim
 *  with `grapheme: null`; answer with the crop's grapheme slot and the grapheme the save took back. */
export async function setGrapheme(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) {
    const { crop } = await withCrop(env, saved, ledger.alias);
    if (crop == null) tools.fail(404, NOT_HERE);
    // A pick sent again files the crop's runs again, in case the first send stopped before it did.
    await ledger.refile?.(crop);
    return { id: publicId(crop), grapheme_claim: (await cropGraphemes(env, [crop])).get(crop) ?? null, replaced: saved.replaced ?? null };
  }
  let member: Member | null = null;
  if (input.grapheme !== null) {
    const grapheme = await claimedHead(env, input.grapheme, tools);
    member = { object: grapheme, value: null, confidence: null, confidence_scheme: null };
    checkClaims('has_grapheme', [member], { [grapheme]: 'grapheme' }, tools.fail);
  }
  const crop = await ledger.crop(env, named);
  const own = await env.ATLAS.prepare(ownClaimQuery('has_grapheme', ownedActors(actor))).bind(crop.id, actor).first<Own>();
  if (!member && !own) tools.fail(409, 'You have no grapheme on this crop to clear.');
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString();
  const plan = member ? await planClaim(env, { key, actor, crop, predicate: 'has_grapheme', scope: '', members: [member], at, method: 'crop-page' }, ledger)
    : await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger);
  const result = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, cut: crop.cut, replaced: own?.object ?? null }, ledger);
  return { id: publicId(crop.id), grapheme_claim: (await cropGraphemes(env, [crop.id])).get(crop.id) ?? null, replaced: result.replaced ?? null };
}

/** Say what structure one crop is built as where no recorded form names its shape (named as `setStructure`
 *  names one), or take back the reader's own claim with no structure named. The reader's own form on the
 *  crop is taken back by the same save: it would name the shape this one leaves open. A form chosen later
 *  leaves the structure. Answers the crop's structure and form slots, the structure the save took back,
 *  and the value of the form it took back (`replaced_form`). */
export async function setCropStructure(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const answer = async (crop: number, replaced: string | null, form: string | null) => ({ id: publicId(crop), structure: (await cropStructures(env, [crop])).get(crop) ?? null,
    form: await formOfCrop(env, crop), replaced, replaced_form: form });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) {
    const { crop } = await withCrop(env, saved, ledger.alias);
    if (crop == null) tools.fail(404, NOT_HERE);
    return answer(crop, saved.replaced ?? null, saved.form ?? null);
  }
  const found = input.char == null && input.structure == null ? null : await namedStructure(env, input, tools, ctx, true);
  const crop = await ledger.crop(env, named), owned = ownedActors(actor);
  const [own, form] = await Promise.all((['built_as', 'has_form'] as const).map(predicate => env.ATLAS.prepare(ownClaimQuery(predicate, owned)).bind(crop.id, actor).first<Own>()));
  if (!found && !own) tools.fail(409, 'You have no structure on this crop to clear.');
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [], crops: [crop.id] };
  const add = (part: { statements: D1PreparedStatement[]; keys: Plan['keys'] }) => { plan.statements.push(...part.statements); plan.keys.push(...part.keys) };
  if (!found) add(await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger));
  else {
    const { structure, source } = found;
    const member: Member = { object: structure.id, value: null, confidence: null, confidence_scheme: null };
    checkClaims('built_as', [member], { [structure.id]: 'structure' }, tools.fail);
    if (source) plan.statements.push(structureRow(env, structure, source, actor, at));
    const claim = await planClaim(env, { key, actor, crop, predicate: 'built_as', scope: '', members: [member], at, method: 'form-picker' }, ledger);
    add(claim);
    // Every form of the reader's own, as a form claim takes back each earlier one.
    if (form) add({ statements: [retractOwn(env, { key, actor, subject: crop.text, predicate: 'has_form', scope: '', at, by: claim.assertions[0], owned })],
      keys: [[crop.text, 'has_form', '', '']] });
  }
  const replaced = { replaced: own?.object ?? null, form: found ? form?.text ?? null : null };
  const result = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, cut: crop.cut, ...replaced }, ledger);
  return answer(crop.id, result.replaced ?? null, result.form ?? null);
}

// A form's `written_for` claims: the graphemes the form writes. A claim is not about a crop, so it has
// no evidence version and survives every recut.
const FORM_ID = /^fm:[0-9a-f]{32}$/;
const FILED = new Set(['asserted', 'accepted', 'adjudicated']);
export const writtenForQuery = () => `SELECT c.object AS grapheme,c.status,c.members,c.claims
  FROM current_claims c WHERE c.subject=? AND c.predicate='written_for' AND c.scope=''`;
/** The graphemes a form is written for, each with who says so now: a form of several graphemes has one
 *  row each. A slot people disagree on holds no grapheme until it is adjudicated. */
export async function writtenForOf(env: Env, form: string): Promise<{ grapheme: string; char: string; by: string[] }[]> {
  const rows = (await env.ATLAS.prepare(writtenForQuery()).bind(form).all<{ grapheme: string; status: string; members: string; claims: string }>()).results;
  return rows.filter(row => FILED.has(row.status)).flatMap(row => {
    const by = [...new Set((JSON.parse(row.claims) as Json[]).filter(c => c.standing).map(c => c.asserted_by as string))];
    return [...new Set((JSON.parse(row.members) as Json[]).flatMap(m => m.object ? [m.object as string] : []))]
      .map(grapheme => ({ grapheme, char: keyText(grapheme), by }));
  }).sort((a, b) => a.grapheme < b.grapheme ? -1 : a.grapheme > b.grapheme ? 1 : 0);
}
// The actor's own live claim that this form writes this grapheme: one range of `assertion_object`.
export const ownWrittenForQuery = (owned: string) => `SELECT a.id FROM assertions a WHERE a.subject=? AND a.predicate='written_for'
  AND a.scope='' AND a.slot=? AND (a.asserted_by=? OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.rowid DESC LIMIT 1`;
/** Say that a form is written for a grapheme (`remove` false), or take back the reader's own claim
 *  (`remove` true). A form can be written for several graphemes; each is a claim of its own. The answer
 *  names the grapheme claimed and the form's graphemes now, so a picker can show the form on another crop
 *  of the family. */
export async function setWrittenFor(env: Env, input: Json, form: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ form, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: form, grapheme: (await keyHeads(env, [input.grapheme])).get(input.grapheme), written_for: await writtenForOf(env, form), replaced: saved.replaced ?? null };
  if (!FORM_ID.test(form) || !await env.ATLAS.prepare('SELECT 1 FROM forms WHERE id=?').bind(form).first()) tools.fail(404, 'No such form.');
  const named = input.grapheme == null ? null : tools.text(input.grapheme, 64, 'grapheme', true)!;
  if (!graphemeKey(named)) return tools.fail(422, 'Name a grapheme by its code points.');
  const remove = input.remove === true;
  const ownOf = (grapheme: string) => env.ATLAS.prepare(ownWrittenForQuery(ownedActors(actor))).bind(form, grapheme, actor).first<{ id: string }>();
  // A claim is taken back by the key it was recorded under, or by one that key heads.
  let grapheme = remove ? named : await claimedHead(env, named, tools), own = await ownOf(grapheme);
  if (remove && !own) {
    const head = (await keyHeads(env, [named])).get(named)!;
    if (head !== named) { grapheme = head; own = await ownOf(head); }
  }
  const linked = (await writtenForOf(env, form)).some(value => value.grapheme === grapheme);
  if (remove && !own) tools.fail(409, `You have not said this form writes ${keyText(grapheme)}.`);
  const at = new Date().toISOString();
  // A claim already standing is not made again; a picker that lost the answer is told what holds, and
  // the answer is saved so a retry reads it.
  if (!remove && own && linked) {
    await env.ATLAS.prepare('INSERT OR IGNORE INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)')
      .bind(key, actor, signature, JSON.stringify({ submission: key, subject: form, replaced: null }), at).run();
    return { id: form, grapheme, written_for: await writtenForOf(env, form), replaced: null };
  }
  const plan: Plan = { statements: [], keys: [] };
  if (own) {
    const retract = await planAction(env, { key: key + '/retract', actor, target: own.id, action: 'retract',
      reason: remove ? 'cleared' : 'replaced', admin: false, at }, ledger);
    plan.statements.push(...retract.statements); plan.keys.push(...retract.keys);
  }
  if (!remove) {
    const member: Member = { object: grapheme, value: null, confidence: null, confidence_scheme: null };
    checkClaims('written_for', [member], { [grapheme]: 'grapheme' }, tools.fail);
    const claim = await planSubjectClaim(env, { key, actor, subject: form, predicate: 'written_for', scope: '', members: [member],
      at, method: 'form-picker' }, ledger);
    plan.statements.push(...claim.statements); plan.keys.push(...claim.keys);
  }
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, subject: form, replaced: remove ? grapheme : null }, ledger);
  return { id: form, grapheme, written_for: await writtenForOf(env, form), replaced: remove ? grapheme : null };
}

// A form's `has_structure` claims: the 構形 the form is filed under (docs/decisions/0012-structure-layer.md).
// A structure is named by a character that has it, or by a description the reference load read (a
// form's own, when a reviewer typed one), since the Worker reads structures and derives none; one a
// claim has already named is also named by its id.
export const structuresQuery = () => `SELECT c.status,c.members,c.claims FROM current_claims c
  WHERE c.subject=? AND c.predicate='has_structure' AND c.scope=''`;
export { structureLabelsQuery };
/** The structures a form is filed under, each with its label and who says so now. */
export async function structuresOf(env: Env, form: string): Promise<{ structure: string; label: string; by: string[] }[]> {
  const rows = (await env.ATLAS.prepare(structuresQuery()).bind(form).all<{ status: string; members: string; claims: string }>()).results;
  const found = rows.filter(row => FILED.has(row.status)).flatMap(row => {
    const by = [...new Set((JSON.parse(row.claims) as Json[]).filter(c => c.standing).map(c => c.asserted_by as string))];
    return [...new Set((JSON.parse(row.members) as Json[]).flatMap(m => m.object ? [m.object as string] : []))].map(structure => ({ structure, by }));
  });
  if (!found.length) return [];
  const labels = new Map((await env.ATLAS.prepare(structureLabelsQuery()).bind(JSON.stringify(found.map(value => value.structure)))
    .all<{ id: string; label: string }>()).results.map(row => [row.id, row.label]));
  return found.map(value => ({ ...value, label: labels.get(value.structure) ?? '' }))
    .sort((a, b) => a.structure < b.structure ? -1 : a.structure > b.structure ? 1 : 0);
}
// The structures claimed for each of these forms: one range of `current_claims`' key a form.
export const claimedStructuresQuery = () => `SELECT c.subject AS form,c.object AS structure,c.status,c.claims,s.label
  FROM json_each(?) j JOIN current_claims c ON c.subject=j.value AND c.predicate='has_structure' AND c.scope=''
  LEFT JOIN structures s ON s.id=c.object`;
/** The form a written value names, or null for one that names none. */
export async function formNamed(value: string): Promise<string | null> {
  const representation = typed(value);
  return typeof representation === 'string' ? null : anchoredForm(await representationId(representation));
}
/** The 構形 readers filed each of these written values under, each with who says so now: first the
 *  one most readers name, the lowest id among equals. A value nobody filed is not in the answer. */
export async function claimedStructures(env: Env, names: string[]): Promise<Map<string, { id: string; label: string; by: string[] }[]>> {
  const forms = new Map<string, string[]>();
  for (const name of names) {
    const form = await formNamed(name);
    if (form) forms.set(form, [...forms.get(form) ?? [], name]);
  }
  const found = new Map<string, { id: string; label: string; by: string[] }[]>();
  if (!forms.size) return found;
  const rows = (await env.ATLAS.prepare(claimedStructuresQuery()).bind(JSON.stringify([...forms.keys()]))
    .all<{ form: string; structure: string | null; status: string; claims: string; label: string | null }>()).results;
  for (const row of rows) {
    if (!FILED.has(row.status) || !row.structure || row.label == null) continue;
    const by = [...new Set((JSON.parse(row.claims) as Json[]).filter(c => c.standing).map(c => c.asserted_by as string))];
    for (const name of forms.get(row.form) ?? []) found.set(name, [...found.get(name) ?? [], { id: row.structure, label: row.label, by }]);
  }
  for (const filed of found.values()) filed.sort((a, b) => b.by.length - a.by.length || (a.id < b.id ? -1 : 1));
  return found;
}
// The actor's own live claims of a form's structures, latest first: one range of `assertion_slot`.
export const ownStructuresQuery = (owned: string) => `SELECT a.id,a.slot FROM assertions a WHERE a.subject=? AND a.predicate='has_structure'
  AND a.scope='' AND (a.asserted_by=? OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.rowid DESC`;
/** The id `glyph_atlas.structures` gives a label under a scheme and a policy. */
export async function structureId(label: string, scheme: string, policy: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`structure\n${label}\n${scheme}\n${policy}`));
  return 'st:' + [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('').slice(0, 32);
}
const STRUCTURE_ID = /^st:[0-9a-f]{32}$/;
type StructureSource = NonNullable<Awaited<ReturnType<typeof structureSource>>>;
/** The structure a write names: by `char`, a character that has it or a description the load read
 *  (with `of`, a regional shape of that character; with `head`, the structure that one stands under as
 *  a layout of it), or by `structure`, the id of one a claim has
 *  named before, which `known` requires to have its row. `source` is what a new row is written
 *  under, null for a structure named by id. */
async function namedStructure(env: Env, input: Json, tools: ClaimTools, ctx: ExecutionContext | undefined, known: boolean):
  Promise<{ structure: { id: string; label: string }; source: StructureSource | null }> {
  if (input.structure != null) {
    const id = tools.text(input.structure, 64, 'structure', true)!;
    if (!STRUCTURE_ID.test(id)) tools.fail(422, 'Name a structure by its id.');
    const row = known ? await env.ATLAS.prepare('SELECT label FROM structures WHERE id=?').bind(id).first<{ label: string }>() : null;
    if (known && !row) tools.fail(422, 'No claim has named this structure.');
    return { structure: { id, label: row?.label ?? '' }, source: null };
  }
  const char = tools.text(input.char, FORM_TEXT_LONGEST, 'char', true)!;
  if (written(char).length !== 1 || written(char)[0] !== char) tools.fail(422, 'Name one character or description.');
  // A character's regional shape is no key of the load: it is found among the shapes of `of`.
  const of = input.of == null ? null : tools.text(input.of, 8, 'of', true)!;
  const [described, read] = await Promise.all([characterIds(env, structureKey(of ?? char), ctx), structureSource(env, ctx)]);
  // No policy object: no reference load has carried structures yet.
  const source = read ?? tools.fail(503, 'The structures are being loaded.');
  // A regional shape is a spelling of its character's structure.
  if (of !== null && !(described?.shapes ?? []).some(one => one.text === char)) tools.fail(422, `${char} is no shape of ${of}.`);
  const structure: { id: string; label: string } = described?.structure ?? tools.fail(422, `No structure is known for ${char}.`);
  // A shard and a policy from two loads would name a row its id does not describe.
  if (await structureId(structure.label, source.scheme, source.policy) !== structure.id) tools.fail(503, 'The structures are being loaded.');
  if (input.head !== true) return { structure, source };
  const label: string = headOf(structure.label) ?? tools.fail(422, `${char} is no layout of another structure.`);
  return { structure: { id: await structureId(label, source.scheme, source.policy), label }, source };
}
const structureRow = (env: Env, structure: { id: string; label: string }, source: StructureSource, actor: string, at: string) =>
  env.ATLAS.prepare('INSERT OR IGNORE INTO structures(id,label,scheme,version,policy,created_by,created_at) VALUES(?,?,?,?,?,?,?)')
    .bind(structure.id, structure.label, source.scheme, source.version, source.policy, actor, at);
/** File a form under the structure of a character or a description, or take back the reader's own
 *  claim (`remove`). The form is named by its id or by its written value (a character, a character
 *  with a variation selector, a description); a value nobody has chosen yet gets its form here. The
 *  structure is named by `char` (with `of`, a regional shape of that character), or by `structure`,
 *  the id of one a claim has named before: a claim made under an earlier policy is one no character
 *  names any more. A form can be filed under several
 *  structures, each a claim of its own; with `replace` the reader's claims of its other structures
 *  are taken back in the same save, so the form moves. `replaced` lists the structures of the claims
 *  taken back. A structure's row is written with its first claim, under the version the policy object
 *  names at that moment. */
export async function setStructure(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ form: named, structure: input });
  let representation: Representation | null = null;
  if (!FORM_ID.test(named)) {
    const value = typed(tools.literal(tools.text(named, FORM_TEXT_LONGEST, 'form', true)!));
    if (typeof value === 'string') tools.fail(422, PROBLEMS[value]);
    representation = value as Representation;
  }
  const form = representation ? await anchoredForm(await representationId(representation)) : named;
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: form, structures: await structuresOf(env, form), replaced: saved.replaced ?? [] };
  const remove = input.remove === true, replace = input.replace === true && !remove;
  const exists = Boolean(await env.ATLAS.prepare('SELECT 1 FROM forms WHERE id=?').bind(form).first());
  if (!exists && !representation) tools.fail(404, 'No such form.');
  const { structure, source } = await namedStructure(env, input, tools, ctx, !remove);
  const mine = exists ? (await env.ATLAS.prepare(ownStructuresQuery(ownedActors(actor))).bind(form, actor).all<{ id: string; slot: string }>()).results : [];
  const own = mine.find(claim => claim.slot === structure.id);
  const others = replace ? mine.filter(claim => claim.slot !== structure.id) : [];
  const held = exists && (await structuresOf(env, form)).some(value => value.structure === structure.id);
  if (remove && !own) tools.fail(409, 'You have not said this form has this structure.');
  const at = new Date().toISOString();
  if (!remove && own && held && !others.length) {
    await env.ATLAS.prepare('INSERT OR IGNORE INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)')
      .bind(key, actor, signature, JSON.stringify({ submission: key, subject: form, replaced: [] }), at).run();
    return { id: form, structures: await structuresOf(env, form), replaced: [] };
  }
  const plan: Plan = { statements: [], keys: [] };
  const again = Boolean(own) && !(held && !remove);
  const taken = [...(again ? [own!] : []), ...others];
  for (const claim of taken) {
    const retract = await planAction(env, { key: key + '/retract', actor, target: claim.id, action: 'retract',
      reason: remove ? 'cleared' : 'replaced', admin: false, at }, ledger);
    plan.statements.push(...retract.statements); plan.keys.push(...retract.keys);
  }
  if (!remove && (!own || again)) {
    if (!exists) await planNaming(env, { key, actor, representation: representation!, at }, plan);
    const member: Member = { object: structure.id, value: null, confidence: null, confidence_scheme: null };
    checkClaims('has_structure', [member], { [structure.id]: 'structure' }, tools.fail);
    if (source) plan.statements.push(structureRow(env, structure, source, actor, at));
    const claim = await planSubjectClaim(env, { key, actor, subject: form, predicate: 'has_structure', scope: '', members: [member],
      at, method: 'form-picker' }, ledger);
    plan.statements.push(...claim.statements); plan.keys.push(...claim.keys);
  }
  const replaced = remove ? [structure.id] : [...new Set(others.map(claim => claim.slot))];
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, subject: form, replaced }, ledger);
  return { id: form, structures: await structuresOf(env, form), replaced };
}

// A structure's `folds_into` claim: a reader's word that it is one 構形 with another and stands under
// it, so the forms of both are grouped under the other's label. A structure folds into one other,
// and that one may fold further; a slot readers disagree on folds nowhere.
const FOLD_DEPTH = 8;
const FOLD = `c.predicate='folds_into' AND c.scope='' AND c.slot='' AND c.status IN ('asserted','accepted','adjudicated') AND c.object IS NOT NULL`;
// The folds that lead on from these structures, each found by `current_claims`' key, with how far
// from a named structure each was met.
export const foldsQuery = () => `WITH RECURSIVE reach(id,depth) AS (SELECT j.value,0 FROM json_each(?) j
    UNION SELECT c.object,reach.depth+1 FROM reach JOIN current_claims c ON c.subject=reach.id AND ${FOLD} WHERE reach.depth<${FOLD_DEPTH})
  SELECT c.subject AS id,c.object AS head,c.claims,s.label,max(reach.depth) AS depth FROM reach JOIN current_claims c ON c.subject=reach.id AND ${FOLD}
  LEFT JOIN structures s ON s.id=c.object GROUP BY c.subject`;
// A chain longer than one read follows is read on from where the read stopped, this many times.
const FOLD_READS = 4;
export type Fold = { head: { id: string; label: string }; by: string[]; path: string[] };
/** Where each of these structures stands now: the structure at the end of its folds, with who folded
 *  it (its own fold, not the ones after it) and the structures its folds pass through, the end
 *  among them (`path`). A structure that folds nowhere is not in the answer; folds that lead round
 *  stop at the lowest id among them. */
export async function foldsOf(env: Env, ids: string[]): Promise<Map<string, Fold>> {
  const found = new Map<string, Fold>();
  type Row = { id: string; head: string; claims: string; label: string | null; depth: number };
  const rows = new Map<string, Row>();
  for (let from = [...new Set(ids)], read = 0; from.length && read < FOLD_READS; read++) {
    const more = (await env.ATLAS.prepare(foldsQuery()).bind(JSON.stringify(from)).all<Row>()).results;
    for (const row of more) rows.set(row.id, row);
    // A fold met at the last step leads to a structure whose own fold this read did not look for.
    from = [...new Set(more.filter(row => row.depth >= FOLD_DEPTH && !rows.has(row.head)).map(row => row.head))];
  }
  const labels = new Map([...rows.values()].map(row => [row.head, row.label ?? '']));
  for (const id of new Set(ids)) {
    const first = rows.get(id);
    if (!first) continue;
    const seen = [id];
    while (rows.has(seen.at(-1)!) && !seen.includes(rows.get(seen.at(-1)!)!.head)) seen.push(rows.get(seen.at(-1)!)!.head);
    // Round a ring every structure of it answers alike.
    const back = rows.get(seen.at(-1)!)?.head, end = back === undefined ? seen.at(-1)! : seen.slice(seen.indexOf(back)).sort()[0];
    if (end === id) continue;
    found.set(id, { head: { id: end, label: labels.get(end) ?? '' }, path: seen.slice(1),
      by: [...new Set((JSON.parse(first.claims) as Json[]).filter(c => c.standing).map(c => c.asserted_by as string))] });
  }
  return found;
}
// The label of the structure another layout of the same parts stands under: 辵 beside X (⿰辵禺) or
// whole under it (⿺辵土) under ⿺辶X. `glyph_atlas.structures.head` answers the same.
const headOf = (label: string) => /^[⿰⿺]辵/u.test(label) ? '⿺辶' + label.slice(2) : label.startsWith('⿲辵') ? '⿺辶⿰' + label.slice(2) : null;
type Named = { id: string; label: string };
/** Where each named structure stands, null for one that stands on its own: at the end of the readers'
 *  folds and of the layouts, a reader's fold first. A layout whose head a reader folded into it stands
 *  on its own. `source` gives what the structures were read under, without which no layout is known. */
export async function standingOf(named: Named[], source: () => Promise<{ scheme: string; policy: string } | null>, folds: (ids: string[]) => Promise<Map<string, Fold>>) {
  const heads = new Map<string, Named>(), folded = new Map<string, Fold>(), asked = new Set<string>();
  for (let reading = named; reading.length;) {
    for (const { id, label } of reading) {
      const head = heads.has(id) ? null : headOf(label), read = head && await source();
      if (head && read) heads.set(id, { id: await structureId(head, read.scheme, read.policy), label: head });
    }
    const wanted = [...new Set(reading.flatMap(({ id }) => [id, heads.get(id)?.id ?? id]))].filter(id => !asked.has(id));
    for (const id of wanted) asked.add(id);
    const more = wanted.length ? await folds(wanted) : new Map<string, Fold>();
    for (const [id, fold] of more) folded.set(id, fold);
    reading = [...more.values()].map(fold => fold.head).filter(head => !asked.has(head.id));
  }
  return (structure: Named): { head: Named; by: string[] } | null => {
    const path: Named[] = [structure];
    for (;;) {
      const at = path.at(-1)!.id, fold = folded.get(at), head = heads.get(at);
      const next = fold?.head ?? (head && !folded.get(head.id)?.path.includes(at) ? head : null);
      if (!next) break;
      const seen = path.findIndex(one => one.id === next.id);
      if (seen >= 0) { path.push(path.slice(seen).sort((a, b) => a.id < b.id ? -1 : 1)[0]); break; }
      path.push(next);
    }
    const head = path.at(-1)!;
    return head.id === structure.id ? null : { head: { id: head.id, label: head.label }, by: folded.get(structure.id)?.by ?? [] };
  };
}
// The actor's own live fold of a structure: one range of `assertion_slot`.
export const ownFoldQuery = (owned: string) => `SELECT a.id,a.object FROM assertions a WHERE a.subject=? AND a.predicate='folds_into'
  AND a.scope='' AND a.slot='' AND (a.asserted_by=? OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.rowid DESC LIMIT 1`;
/** Fold one structure into another (`from`, `into`: each named as `setStructure` names one), or with
 *  `remove` take back the reader's own fold of `from`. A fold that would lead back to `from` is
 *  refused. A reader's new fold of a structure replaces their earlier one. Answers the structure,
 *  where it stands now (`folds_into`, null for nowhere) and `replaced`, the structure the reader's
 *  own earlier fold named. */
export async function setFold(env: Env, input: Json, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ fold: input });
  const answer = async (structure: string, replaced: string | null) => {
    const fold = (await foldsOf(env, [structure])).get(structure);
    return { structure, folds_into: fold ? { ...fold.head, by: fold.by } : null, replaced };
  };
  // A save sent again answers for the structure it folded, whatever its name gives now.
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return answer(saved.subject, saved.replaced ?? null);
  const remove = input.remove === true;
  const part = (value: unknown): Json => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Json : tools.fail(422, 'Name a structure.');
  const from = await namedStructure(env, part(input.from), tools, ctx, false);
  const own = await env.ATLAS.prepare(ownFoldQuery(ownedActors(actor))).bind(from.structure.id, actor).first<{ id: string; object: string }>();
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [] };
  let replaced = own?.object ?? null;
  if (remove) {
    if (!own) tools.fail(409, 'You have not folded this structure.');
    const retract = await planAction(env, { key: key + '/retract', actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger);
    plan.statements.push(...retract.statements); plan.keys.push(...retract.keys);
  } else {
    const into = await namedStructure(env, part(input.into), tools, ctx, true);
    if (into.structure.id === from.structure.id) tools.fail(422, 'A structure folds into another.');
    // Anywhere on the way from `into`, not only at its end: the reader's new fold takes back their
    // earlier one, which may be what leads on from `from` today.
    if ((await foldsOf(env, [into.structure.id])).get(into.structure.id)?.path.includes(from.structure.id)) tools.fail(409, 'That structure already stands under this one.');
    if (!from.source && !await env.ATLAS.prepare('SELECT 1 FROM structures WHERE id=?').bind(from.structure.id).first()) tools.fail(422, 'No claim has named this structure.');
    // The reader's fold as it stands is not made again: a new claim would drop what others said of it.
    if (own?.object !== into.structure.id) {
      const member: Member = { object: into.structure.id, value: null, confidence: null, confidence_scheme: null };
      checkClaims('folds_into', [member], { [into.structure.id]: 'structure' }, tools.fail);
      for (const one of [from, into]) if (one.source) plan.statements.push(structureRow(env, one.structure, one.source, actor, at));
      // A slot of one value: the claim takes back the reader's earlier fold of the structure itself.
      const claim = await planSubjectClaim(env, { key, actor, subject: from.structure.id, predicate: 'folds_into', scope: '', members: [member],
        at, method: 'form-picker' }, ledger);
      plan.statements.push(...claim.statements); plan.keys.push(...claim.keys);
    } else replaced = null;
  }
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, subject: from.structure.id, replaced }, ledger);
  return answer(from.structure.id, replaced);
}

// The reader's own live readings, rubies and words on these crops, with the save each came from: ranges of `assertion_crop`.
const RUN_PREDICATES = [...READING_PREDICATES, 'writes_word'];
export const ownReadingsQuery = (owned: string) => `SELECT a.id,a.crop,a.subject,a.predicate,a.object,a.value,a.confidence,a.confidence_scheme,a.method,a.cut,a.submission,
  (SELECT e.ref FROM assertion_evidence e WHERE e.assertion=a.id AND e.kind='crop') AS version FROM json_each(?1) j
  JOIN assertions a ON a.crop=j.value AND a.predicate IN (${RUN_PREDICATES.map(p => `'${p}'`).join()}) WHERE a.scope='' AND a.slot=''
  AND (a.asserted_by=?2 OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`;
export const savedRunsQuery = () => 'SELECT response FROM ledger_submissions WHERE id IN (SELECT value FROM json_each(?))';
type OwnReading = { id: string; crop: number; subject: string; predicate: string; object: string | null; value: string | null;
  confidence: number | null; confidence_scheme: string | null; method: string | null; cut: number; submission: string; version: string | null };
/** Set what stands beside a crop at its place: the run of characters it stands in (`run`, with the crop at
 *  `at`), the `ruby` and the 左訓 (`left`) as the page writes them, and the reader's note of how the run is
 *  read (`reading`, over each character or over all of them) with what it rests on (`method`). Any of the
 *  three may be left out, and one is given, or a word. The other crops of the run (`others`, each with its place and the
 *  version its reader saw) are claimed in the same write. `word` ({id, form, method}) names the dictionary word
 *  the run writes, on each of its crops (decision 0004): null takes the reader's back, and one without a method
 *  keeps the method the reader gave it. Without `word`, the word the reader holds on the crop goes with the run
 *  as they hold it. A save replaces the reader's earlier runs wherever
 *  they meet the new one, whole; with `run` null it only takes the reader's run on this crop back. Saved or
 *  refused whole. */
export async function setReading(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(env, tools, ctx), spec = PREDICATES.writes_word;
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const answer = async (crop: number, crops: number[]) => ({ id: publicId(crop), reading: (await cropReadings(env, [crop])).get(crop) ?? null,
    word: await wordOfCrop(env, crop), crops: crops.map(publicId) });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return answer(saved.crop, saved.crops ?? []);
  const clear = input.run === null, others: Json[] = clear ? [] : input.others ?? [];
  const bad = (): never => tools.fail(422, 'Name the characters that stand together and what is written beside them or how they are read.');
  if (!Array.isArray(others) || others.length >= RUN_MAX || others.some(other => typeof other !== 'object' || other === null)) bad();
  const at = new Date().toISOString(), places: number[] = [input.at, ...others.map(other => other.at)];
  const member = (value: unknown): Member => ({ object: null, value, confidence: null, confidence_scheme: null });
  // What each crop of the run is claimed as, checked before any crop is read.
  type Claimed = { predicate: string; method: string | null; members: Member[] };
  if (!clear && (!Array.isArray(input.run) || input.run.length > RUN_MAX || places.some(place => !Number.isSafeInteger(place) || place < 0 || place >= input.run.length))) bad();
  if (!clear && input.reading == null && input.ruby == null && input.left == null && input.word == null) bad();
  const claims: Claimed[][] = clear ? [] : places.map(place => [
    ...(input.reading == null ? [] : [{ predicate: 'read_as', method: input.method as string | null, members: [member({ run: input.run, at: place, reading: input.reading })] }]),
    ...([['bears_ruby', input.ruby], ['bears_left_ruby', input.left]] as const).flatMap(([predicate, ruby]) =>
      ruby == null ? [] : [{ predicate, method: null, members: [member({ run: input.run, at: place, ruby })] }])]);
  for (const each of claims.flat()) checkClaims(each.predicate, each.members, {}, tools.fail, each.method);
  if (!clear && input.ruby != null && input.reading != null && input.ruby.length !== input.reading.length) tools.fail(422, 'The ruby is split as the reading is.');
  const word = input.word as { id?: unknown; form?: unknown; method?: unknown } | null | undefined;
  if (word != null && clear) bad();
  if (word != null) {
    if (typeof word !== 'object' || typeof word.id !== 'string' || !await env.ATLAS.prepare('SELECT 1 FROM words WHERE id=?').bind(word.id).first()) tools.fail(422, 'No such word.');
    if (word.form !== 'certain' && word.form !== 'probable') tools.fail(422, 'Say whether the form is certain or probable.');
  }
  if (new Set(places).size !== places.length) tools.fail(422, 'Each place of the run is one crop.');
  const crops = await Promise.all([named, ...others.map(other => other.id)].map(one => ledger.crop(env, one)));
  if (new Set(crops.map(crop => crop.id)).size !== crops.length) tools.fail(422, 'Each place of the run is one crop.');
  const recut = crops.filter((crop, i) => !crop.version || (i ? others[i - 1].crop_version : input.crop_version) !== crop.version);
  if (recut.length) tools.fail(409, 'Some of these crops were cut again. Reload them.', { targets: recut.map(crop => ({ id: publicId(crop.id) })) });
  // The reader's earlier runs on these crops, and every crop each of them was saved on.
  const own = async (ids: number[]) => (await env.ATLAS.prepare(ownReadingsQuery(ownedActors(actor)))
    .bind(JSON.stringify(ids), actor).all<OwnReading>()).results;
  const ids = crops.map(crop => crop.id), here = await own(ids);
  const isWord = (claim: OwnReading) => claim.predicate === 'writes_word';
  if (clear && !here.some(claim => word === null || !isWord(claim))) tools.fail(409, 'You have no reading on this crop to clear.');
  if (word != null) {
    const method = word.method ?? here.find(claim => isWord(claim) && claim.crop === crops[0].id && claim.object === word.id)?.method ?? input.method;
    const named: Member = { object: word.id as string, value: null, confidence: spec.confidence!.form[word.form as string], confidence_scheme: 'form' };
    checkClaims('writes_word', [named], { [word.id as string]: 'word' }, tools.fail, method as string);
    for (const list of claims) list.push({ predicate: 'writes_word', method: method as string, members: [named] });
  }
  // The word the reader holds on the crop's cut now, its alternatives and its certainty with it, claimed on each
  // other crop of the run that does not already hold the same.
  const held = (crop: CropNow) => here.filter(claim => isWord(claim) && claim.crop === crop.id && restsOn(crop, claim.cut, claim.version));
  const worded = (list: OwnReading[]) => tools.canonical(list.map(one => [one.object, one.value, one.confidence, one.confidence_scheme, one.method]).sort());
  const carried = word === undefined && !clear ? held(crops[0]) : [];
  if (carried.length) for (const [i, crop] of crops.entries()) if (worded(held(crop)) !== worded(carried)) claims[i].push({ predicate: 'writes_word', method: carried[0].method,
    members: carried.map(one => ({ object: one.object, value: one.value === null ? null : JSON.parse(one.value), confidence: one.confidence, confidence_scheme: one.confidence_scheme })) });
  const saves = new Set(here.map(claim => claim.submission));
  const reached = saves.size ? (await env.ATLAS.prepare(savedRunsQuery()).bind(JSON.stringify([...saves])).all<{ response: string }>())
    .results.flatMap(row => (JSON.parse(row.response).crops ?? []) as number[]) : [];
  const beyond = [...new Set(reached)].filter(crop => !ids.includes(crop));
  // A word that went with the run was claimed by whichever save first took it there, so it is known by what it names.
  const told = new Set(here.filter(isWord).map(claim => tools.canonical([claim.object, claim.value])));
  const earlier = [...here, ...(beyond.length ? await own(beyond) : []).filter(claim => saves.has(claim.submission)
    || (isWord(claim) && told.has(tools.canonical([claim.object, claim.value]))))];
  const plan: Plan = { statements: [], keys: [], crops: [...new Set([...ids, ...earlier.map(claim => claim.crop)])] };
  for (const [i, crop] of crops.entries()) for (const each of claims[i] ?? []) {
    const part = await planClaim(env, { key, actor, crop, predicate: each.predicate, scope: '', members: each.members, at, method: each.method }, ledger);
    plan.statements.push(...part.statements); plan.keys.push(...part.keys);
  }
  // A claim the new run makes again takes the reader's earlier one back itself (`planClaim`).
  const replacing = new Set(claims.flatMap((list, i) => list.map(each => crops[i].id + each.predicate)));
  // A word the save does not name stays on the run, and leaves the crops the run has left behind with it.
  const leaves = (claim: OwnReading) => !replacing.has(claim.crop + claim.predicate)
    && (!isWord(claim) || word !== undefined || (carried.length > 0 && !ids.includes(claim.crop)));
  for (const claim of earlier.filter(leaves)) {
    plan.statements.push(env.ATLAS.prepare("INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,?,?,'retract',?,?,?)")
      .bind('cf:' + crypto.randomUUID(), key, claim.id, actor, at, clear ? 'cleared' : 'replaced'));
    plan.keys.push([claim.subject, claim.predicate, '', '']);
  }
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crops[0].id, cut: crops[0].cut, crops: ids }, ledger);
  return answer(crops[0].id, ids);
}

// A book's dates on both axes, and its date claims that stand: every `date_*` assertion its source has
// not retracted, found by the book's key in `assertion_slot`.
const AXIS_COLUMNS = 'axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source';
export const bookDatesQuery = () => `SELECT ${AXIS_COLUMNS} FROM book_dates WHERE book=?`;
export const bookDateClaimsQuery = () => `SELECT a.id,a.predicate,a.value,a.tier,e.ref AS source,e.locator
  FROM assertions a LEFT JOIN assertion_evidence e ON e.assertion=a.id AND e.kind='source'
  WHERE a.subject=(SELECT key FROM books WHERE id=?) AND substr(a.predicate,1,5)='date_'
    AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
  ORDER BY a.predicate,a.id`;
const parsed = (value: string | null) => { if (!value) return null; try { return JSON.parse(value) } catch { return null } };
/** A book's dating with the claims it was resolved from, for the inspector. */
export async function bookDates(env: Env, book: number | null): Promise<Json> {
  if (book === null) return { dating: {} };
  const [axes, claims] = await env.ATLAS.batch([env.ATLAS.prepare(bookDatesQuery()).bind(book), env.ATLAS.prepare(bookDateClaimsQuery()).bind(book)]);
  const dating = Object.fromEntries((axes.results as Json[]).map(row => [row.axis, axisOf(row)]));
  const dates = (claims.results as Json[]).map(row => {
    const value = parsed(row.value) ?? {};
    return { id: row.id, kind: String(row.predicate).slice(5), of: value.of, text: value.text, start: value.start, end: value.end,
      precision: value.precision, qualifier: value.qualifier ?? null, uncertain: Boolean(value.uncertain), day: value.day ?? null,
      calendar: value.calendar, hutime: value.conversion?.query ?? null, note: value.note ?? null, tier: row.tier,
      source: row.source, locator: row.locator };
  });
  return { dating, ...(dates.length ? { dates } : {}) };
}

/** A write to the ledger: a claim, an action on one, a crop's form, reading or grapheme, or a fold of a structure. `null` for another path. */
export async function claimWrite(env: Env, request: Request, path: string, me: { id: string; admin: boolean }, tools: ClaimTools,
  limit: () => Promise<void>, ctx?: ExecutionContext): Promise<Json | Response | null> {
  const acted = path.match(/^\/atlas\/claims\/([^/]+)\/actions$/), picked = path.match(/^\/atlas\/characters\/([^/]+)\/(form|reading|grapheme|structure)$/),
    linked = path.match(/^\/atlas\/forms\/([^/]+)\/(written_for|structure)$/);
  if (path !== '/atlas/claims' && path !== '/atlas/forms' && path !== '/atlas/structures/folds' && !acted && !picked && !linked) return null;
  // A claim, an action or a crop's form is a few small rows, and one address is held to a rate.
  await limit();
  const input = await tools.body(request);
  let target = '';
  try { target = decodeURIComponent((acted ?? picked ?? linked)?.[1] ?? '') } catch { tools.fail(404, acted ? 'No such claim.' : linked ? 'No such form.' : NOT_HERE) }
  try {
    if (path === '/atlas/forms') return await setForms(env, input, me.id, tools, ctx);
    if (path === '/atlas/structures/folds') return await setFold(env, input, me.id, tools, ctx);
    if (linked) return linked[2] === 'structure' ? await setStructure(env, input, target, me.id, tools, ctx)
      : await setWrittenFor(env, input, target, me.id, tools, ctx);
    if (picked) return picked[2] === 'reading' ? await setReading(env, input, target, me.id, tools, ctx)
      : picked[2] === 'grapheme' ? await setGrapheme(env, input, target, me.id, tools, ctx)
      : picked[2] === 'structure' ? await setCropStructure(env, input, target, me.id, tools, ctx) : await setForm(env, input, target, me.id, tools, ctx);
    const ledger = ledgerTools(env, tools, ctx);
    return acted ? await actOnClaim(env, input, target, me.id, me.admin, ledger) : await writeClaim(env, input, me.id, ledger);
  } catch (error) {
    if (error instanceof StaleCrop) return staleResponse();
    throw error;
  }
}

// Subjects that are no crop by their shape: form and representation ids, word ids, and book keys.
export const bookKeyQuery = () => 'SELECT 1 FROM books WHERE key=?';
async function cropShaped(env: Env, subject: string) {
  if (/^(fm|rp|st):/.test(subject) || subject.includes('/')) return false;
  return !subject.includes(':') || !await env.ATLAS.prepare(bookKeyQuery()).bind(subject).first();
}

// At most this many crops in one reading of them.
export const READ_MAX = 48;
/** How each of some crops is read now: its version, the line its source gives, and its reading and word. A
 *  crop that is not published is left out. Each crop is a few key lookups, as a claim on it is, and the
 *  readings of them all are one query more. */
export async function readingsOf(env: Env, named: string[]): Promise<Json[]> {
  const ids = [...new Set(named.flatMap(name => cropOf(name) ?? []))];
  const [crops, readings, words] = await Promise.all([Promise.all(ids.map(id => cropNow(env, id))), cropReadings(env, ids), cropWords(env, ids)]);
  return crops.flatMap(crop => crop ? [{ id: publicId(crop.id), crop_version: crop.version, text: crop.line ?? null, reading: readings.get(crop.id) ?? null, word: words.get(crop.id) ?? null }] : []);
}

/** A read of the ledger: a subject's claims, crops' readings, or the export a page at a time. `null` for another path. */
export async function claimRead(env: Env, url: URL, tools: ClaimTools, ctx?: ExecutionContext): Promise<Json | Response | null> {
  const path = url.pathname, q = url.searchParams;
  if (path === '/atlas/ledger' || path === '/atlas/ledger.json') {
    const page = await ledgerPage(env, q, tools.integer);
    return Response.json(page, { headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff',
      ...(path.endsWith('.json') ? { 'content-disposition': 'attachment; filename="atlas-ledger.json"' } : {}) } });
  }
  if (path === '/atlas/readings') {
    const named = (q.get('crops') ?? '').split(',').filter(Boolean);
    if (!named.length || named.length > READ_MAX) tools.fail(422, `Name 1–${READ_MAX} crops.`);
    return { items: await readingsOf(env, named) };
  }
  if (path !== '/atlas/claims') return null;
  const subject = tools.text(q.get('subject'), 512, 'subject', true)!;
  // A crop is named by public id; its old text id moves there. A book, form, representation or word is
  // a key of its own and never reads the alias map.
  if (!await cropShaped(env, subject)) return claimsOf(env, { subject });
  const named = await cropQueryParam(env, subject, ctx);
  if (named && 'moved' in named) return movedQueryResponse(url, 'subject', named.moved);
  if (named) return claimsOf(env, { crop: 'id' in named ? named.id : named.retired });
  return claimsOf(env, { subject });
}
