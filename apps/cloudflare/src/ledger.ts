// The assertion ledger on the site (docs/design/form-model.md), in glyph-atlas-v3 (migration
// 0003_history, decision record 0006). A claim and each action on it are rows written in one D1 batch
// with the resolution of their slot, so `current_claims` always holds what the ledger says. A claim on a
// crop keeps the crop's text id in `subject`, as the moved ledger has it, and names the crop by `crop`
// and its evidence by `(crop, cut)`; a claim on a book, form or word keeps its key in `subject`. The
// catalogue and the resolver are `data/ledger.json`, which the review service reads too.
import catalogue from '../../../data/ledger.json';
import { publicId } from './atlas3';

type Json = Record<string, any>;
type Predicate = { label: string; subject: string; objects: string[]; values?: string[]; value_shape?: string; cardinality: 'one' | 'many';
  alternatives?: boolean; methods?: string[]; confidence?: Record<string, Record<string, number>> };
export const RESOLVER: string = catalogue.resolver;
export const PREDICATES: Record<string, Predicate> = catalogue.predicates as Record<string, Predicate>;
const ACTIONS = new Set<string>(catalogue.actions);
const COLUMNS = 'subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop,cut,resolver,at';
const BODY = (catalogue.resolve as string[]).filter(line => !line.trimStart().startsWith('--')).join('\n');

/** A crop's cut as a claim on it sees it: the published cut, or the next one while a redrawn box waits
 *  in `reviews` for the publication that cuts it (which gives the crop that cut). Two key lookups. */
export const effectiveCut = (crop: string) => `(SELECT c.cut+EXISTS(SELECT 1 FROM reviews rv WHERE rv.crop=c.id AND rv.x IS NOT NULL)
    FROM crops c WHERE c.id=${crop})`;
// The catalogue's resolver compares a claim's crop evidence with the crop's current version, which its
// caller gives as `crop_now`. Here a crop's evidence is its (crop, cut): a claim is live while its
// evidence names its own crop at the crop's effective cut, so a claim on cut -1 never is, nor one on the
// cut a pending redraw replaces; a slot names its crop and that cut beside its key.
const SLOT_CROP = `(SELECT a.crop FROM assertions a WHERE a.subject=k.subject AND a.predicate=k.predicate AND a.scope=k.scope
    AND a.slot=k.slot AND a.crop IS NOT NULL LIMIT 1)`;
const CROP_PARTS: [string, string][] = [
  ['e.ref IS NOT (SELECT c.version FROM crop_now c WHERE c.unit=a.subject)',
    `(e.crop IS NOT a.crop OR e.cut IS NOT ${effectiveCut('a.crop')})`],
  ['(SELECT c.version FROM crop_now c WHERE c.unit=k.subject) AS crop_version,',
    `${SLOT_CROP} AS crop,\n  ${effectiveCut(SLOT_CROP)} AS cut,`],
];
const RESOLVE = CROP_PARTS.reduce((sql, [from, to]) => {
  if (sql.split(from).length !== 2) throw new Error(`data/ledger.json's resolver changed: ${from}`);
  return sql.replace(from, to);
}, BODY);
if (RESOLVE.includes('crop_now')) throw new Error("data/ledger.json's resolver names crop_now where it is not replaced");
// By key: the slots are rows of `current_claims`' primary key, each found by it.
export const resolveClearQuery = () => `DELETE FROM current_claims WHERE (subject,predicate,scope,slot) IN
  (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?1))`;
export const resolveWriteQuery = () => `INSERT INTO current_claims(${COLUMNS}) WITH ${RESOLVE}`;
// At most this many members in one alternative set, and this many claims and actions in a subject's history.
const MEMBERS_MAX = 8, HISTORY_MAX = 200;
// The evidence a claim may name beside its crop, which the ledger writes itself; at most this many items.
const EVIDENCE_KINDS = new Set(['page', 'source']), EVIDENCE_MAX = 8, GLOSS_MAX = 200;
export type Evidence = { kind: string; ref: string; locator: string | null };

/** A crop as a claim on it rests on: its cut now, its evidence version as its record shows it (null
 *  for a crop with no image), its text id (the ledger's subject) and its kind. */
export type CropNow = { id: number; cut: number; version: string | null; text: string; kind: number };
export type LedgerTools = {
  fail: (status: number, message: string, extra?: Json) => never;
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  canonical: (value: unknown) => string;
  // The crop a write names, as its route resolved it, with its version now.
  crop: (env: Env, named: unknown) => Promise<CropNow>;
  // The actor keys one user holds (`actors`), as SQL.
  owned: (user: string) => string;
  // The crop an old text id names, through the alias map, or null.
  alias: (text: string) => Promise<number | null>;
};
export type Member = { object: string | null; value: unknown; confidence: number | null; confidence_scheme: string | null };

/** The actor keys a user holds in glyph-atlas-v3, as SQL; `actor_user` finds them. */
export const ownedActors = (user: string) => `(SELECT key FROM actors WHERE user='${user.replaceAll("'", "''")}')`;

// A value a predicate takes besides its listed ones: a `gloss` is {gloss: text}, the sense in words where no word
// row fits yet, with no space around it and at most GLOSS_MAX code points, as the review service counts them.
const shaped = (shape: string | undefined, value: unknown) => {
  if (shape !== 'gloss' || typeof value !== 'object' || value === null || Array.isArray(value) || Object.keys(value).join() !== 'gloss') return false;
  const gloss = (value as Json).gloss;
  return typeof gloss === 'string' && gloss === gloss.trim() && gloss.length > 0 && [...gloss].length <= GLOSS_MAX;
};

/** Refuse members the catalogue does not allow; `kinds` gives the kind of each named entity. A predicate
 *  that lists `methods` takes one of them, and one that lists `confidence` schemes a confidence at one of their levels. */
export function checkClaims(predicate: string, members: Member[], kinds: Record<string, string>, fail: LedgerTools['fail'],
  method: string | null = null, evidence: Evidence[] = []) {
  const spec = PREDICATES[predicate];
  if (!spec) fail(422, `Unknown predicate ${predicate}.`);
  if (!members.length) fail(422, 'A claim names a value.');
  if (spec.methods && !spec.methods.includes(method as string)) fail(422, `${predicate} names how it is known: one of ${spec.methods.join(', ')}.`);
  if (evidence.length > EVIDENCE_MAX || evidence.some(e => !EVIDENCE_KINDS.has(e.kind) || !e.ref)) fail(422, `Evidence is at most ${EVIDENCE_MAX} pages or sources, each with a reference.`);
  if (new Set(evidence.map(e => JSON.stringify([e.kind, e.ref]))).size < evidence.length) fail(422, 'Each item of evidence is named once.');
  const schemes = spec.confidence;
  if (members.length > 1 && !(spec.alternatives && spec.cardinality === 'one')) fail(422, `${predicate} takes one value a claim.`);
  const seen = new Set<string>();
  for (const m of members) {
    if ((m.object === null) === (m.value === null)) fail(422, 'A claim names an object or a value.');
    if (m.object !== null && !spec.objects.includes(kinds[m.object])) fail(422, `${m.object} cannot be the object of ${predicate}.`);
    if (m.object === null && !(spec.values ?? []).includes(m.value as string) && !shaped(spec.value_shape, m.value)) fail(422, `${JSON.stringify(m.value)} is not a value of ${predicate}.`);
    if ((m.confidence === null) !== (m.confidence_scheme === null)) fail(422, 'A confidence names its scheme.');
    if (schemes && !Object.values(schemes[m.confidence_scheme as string] ?? {}).includes(m.confidence as number))
      fail(422, `${predicate} takes a confidence at one of these levels (${Object.entries(schemes).map(([n, l]) => `${n}: ${Object.keys(l).join(', ')}`).join('; ')}).`);
    if (m.confidence !== null && !Number.isFinite(m.confidence)) fail(422, 'A confidence is a number.');
    const key = JSON.stringify([m.object, m.value]);
    if (seen.has(key)) fail(422, 'The alternatives of a claim differ.');
    seen.add(key);
  }
}
const slotOf = (predicate: string, m: Member, canonical: LedgerTools['canonical']) =>
  PREDICATES[predicate].cardinality === 'one' ? '' : m.object ?? canonical(m.value);

/** The statements that resolve these slots again, to run after the rows that changed them. */
export function resolveSlots(env: Env, keys: [string, string, string, string][]): D1PreparedStatement[] {
  const bound = JSON.stringify(keys);
  return [env.ATLAS.prepare(resolveClearQuery()).bind(bound), env.ATLAS.prepare(resolveWriteQuery()).bind(bound)];
}

// Crops leave the ledger by public id: a crop claim's subject is its crop, and every crop column is.
const outRow = (row: Json) => row.crop == null ? row : { ...row, subject: publicId(row.crop), crop: publicId(row.crop) };
const outEvidence = (items: Json[]) => items.map(e => e.crop == null ? e : { ...e, crop: publicId(e.crop) });
const parseRow = (row: Json) => outRow({ ...row, value: row.value === null ? null : JSON.parse(row.value),
  members: JSON.parse(row.members), supporting: JSON.parse(row.supporting), claims: JSON.parse(row.claims) });

/** A subject the ledger reads: a crop, by its integer id, or a book, form or word, by its key. */
export type Subject = { crop: number } | { subject: string };
// A subject's resolved slots that hold now; a crop's rows resolved on another cut do not. A crop's are
// one range of `current_claim_crop`, another subject's one of the primary key.
export const currentCropClaimsQuery = () => `SELECT c.* FROM current_claims c WHERE c.crop=?1
  AND c.cut IS ${effectiveCut('?1')} ORDER BY c.predicate,c.scope,c.slot`;
export const currentClaimsQuery = () => `SELECT c.* FROM current_claims c WHERE c.subject=? AND c.crop IS NULL
  ORDER BY c.predicate,c.scope,c.slot`;
async function currentOf(env: Env, of: Subject) {
  const statement = 'crop' in of ? env.ATLAS.prepare(currentCropClaimsQuery()).bind(of.crop) : env.ATLAS.prepare(currentClaimsQuery()).bind(of.subject);
  return (await statement.all<Json>()).results.map(parseRow);
}
async function repeat(env: Env, key: string, signature: string, fail: LedgerTools['fail']) {
  const saved = await env.ATLAS.prepare('SELECT request,response FROM ledger_submissions WHERE id=?').bind(key).first<{ request: string; response: string }>();
  if (saved && saved.request !== signature) fail(409, 'This submission was already saved with different values.');
  return saved ? JSON.parse(saved.response) as Json : null;
}
export function submissionId(input: Json, tools: Pick<LedgerTools, 'text' | 'fail'>) {
  const id = tools.text(input.id, 64, 'submission id', true)!;
  if (!/^[0-9a-f-]{36}$/i.test(id)) tools.fail(422, 'Invalid submission id.');
  return id;
}
// A saved response names its crop by integer id and cut (`crop`, `cut`), its other subject by key
// (`subject`); out of the Worker, a crop's subject is its public id.
const subjectOf = (saved: Json): Subject => saved.crop != null ? { crop: saved.crop } : { subject: saved.subject };
const answered = (saved: Json) => {
  const { crop, cut: _cut, ...rest } = saved;
  return crop == null ? rest : { ...rest, subject: publicId(crop) };
};
/** A saved response with its crop: one saved before the cutover that lacks it names its crop by text
 *  id, read through the alias map; a subject the map lacks is no crop. */
export async function withCrop(env: Env, saved: Json, alias: (text: string) => Promise<number | null>): Promise<Json> {
  if (saved.crop != null || typeof saved.subject !== 'string' || !saved.subject.includes(':')) return saved;
  const crop = await alias(saved.subject);
  return crop === null ? saved : { ...saved, crop };
}

// A random id in the shape of a UUID, made in SQL for rows a batch writes by selecting them.
const SQL_UUID = `lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(6)))`;
/** The statement that retracts `actor`'s earlier live claims in a single-valued slot, run in the same
 *  batch after their new claim (`by`), so a concurrent claim of theirs is retracted too. */
export function retractOwn(env: Env, own: { key: string; actor: string; subject: string; predicate: string; scope: string; at: string; by: string; owned: string }) {
  return env.ATLAS.prepare(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason)
    SELECT 'cf:'||${SQL_UUID},?,a.id,'retract',?,?,? FROM assertions a WHERE a.subject=? AND a.predicate=? AND a.scope=? AND a.slot=''
    AND (a.asserted_by=? OR a.asserted_by IN ${own.owned}) AND a.submission IS NOT ? AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`)
    .bind(own.key, own.actor, own.at, 'superseded by ' + own.by, own.subject, own.predicate, own.scope, own.actor, own.key);
}

type Slot = [string, string, string, string];
/** Rows one write adds, and the slots they fall in, to be resolved in the same batch. */
export type Plan = { statements: D1PreparedStatement[]; keys: Slot[] };

// The first claim on a corpus glyph records when the glyph was first written, as its first review
// would (`corpus_rows`, the order the old `units` rows kept).
export const corpusRowQuery = () => 'INSERT OR IGNORE INTO corpus_rows(crop,seq) SELECT ?,coalesce(max(seq),0)+1 FROM corpus_rows';
const CORPUS = 1;

/** The rows of one claim on a crop by `actor` (several members make an alternative set), and the
 *  retraction of the actor's own earlier claims in a slot that takes one value. The claim rests on the
 *  crop's cut now, whose version the caller has checked is the one its reviewer saw. */
export async function planClaim(env: Env, claim: { key: string; actor: string; crop: CropNow; predicate: string; scope: string;
  members: Member[]; tier?: string; method?: string | null; evidence?: Evidence[]; at: string }, tools: LedgerTools) {
  const { key, actor, crop, predicate, scope, members, at } = claim, subject = crop.text;
  const set = members.length > 1 ? 'cf:' + crypto.randomUUID() : null, ids = members.map(() => 'cf:' + crypto.randomUUID());
  const statements: D1PreparedStatement[] = [];
  if (crop.kind === CORPUS) statements.push(env.ATLAS.prepare(corpusRowQuery()).bind(crop.id));
  members.forEach((m, i) => {
    statements.push(env.ATLAS.prepare(`INSERT INTO assertions(id,submission,subject,crop,cut,predicate,scope,slot,object,value,alternative_set,tier,asserted_by,asserted_at,confidence,confidence_scheme,method)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(ids[i], key, subject, crop.id, crop.cut, predicate, scope, slotOf(predicate, m, tools.canonical), m.object,
      m.value === null ? null : tools.canonical(m.value), set, claim.tier ?? 'observed', actor, at, m.confidence, m.confidence_scheme, claim.method ?? null));
    statements.push(env.ATLAS.prepare("INSERT INTO assertion_evidence(assertion,kind,ref,crop,cut) VALUES(?,'crop',?,?,?)")
      .bind(ids[i], crop.version, crop.id, crop.cut));
    for (const e of claim.evidence ?? []) statements.push(env.ATLAS.prepare('INSERT INTO assertion_evidence(assertion,kind,ref,locator) VALUES(?,?,?,?)')
      .bind(ids[i], e.kind, e.ref, e.locator));
  });
  if (PREDICATES[predicate].cardinality === 'one') statements.push(retractOwn(env, { key, actor, subject, predicate, scope, at, by: ids[0], owned: tools.owned(actor) }));
  const keys = [...new Set(members.map(m => slotOf(predicate, m, tools.canonical)))].map((slot): Slot => [subject, predicate, scope, slot]);
  return { statements, keys, assertions: ids };
}

/** The rows of one action by `actor` on claim `target`: a retraction is the asserter's (under any id
 *  they hold), an accept or a reject anyone else's, an adjudication an admin's; retracting one
 *  alternative retracts its set. */
export async function planAction(env: Env, act: { key: string; actor: string; target: string; action: string; reason: string;
  admin: boolean; at: string }, tools: LedgerTools) {
  const { key, actor, target, action, reason, at } = act;
  if (!ACTIONS.has(action)) tools.fail(422, 'Unknown action.');
  const found = await env.ATLAS.prepare(`SELECT a.*,a.asserted_by IN ${tools.owned(actor)} OR a.asserted_by=? AS mine FROM assertions a WHERE a.id=?`)
    .bind(actor, target).first<Json>() ?? tools.fail(404, 'No such claim.');
  if (action === 'retract' && !found.mine) tools.fail(403, 'Only the person who made a claim can retract it.');
  if ((action === 'accept' || action === 'reject') && found.mine) tools.fail(422, 'A claim is already its asserter\'s own; retract it instead.');
  if (action === 'adjudicate' && !act.admin) tools.fail(403, 'Only an adjudicator can decide between claims.');
  const members = action === 'retract' && found.alternative_set
    ? (await env.ATLAS.prepare('SELECT id FROM assertions WHERE alternative_set=? ORDER BY rowid').bind(found.alternative_set).all<{ id: string }>()).results.map(r => r.id)
    : [target];
  const open = action === 'retract' ? (await env.ATLAS.prepare(`SELECT value AS id FROM json_each(?) WHERE NOT EXISTS
    (SELECT 1 FROM assertion_actions x WHERE x.assertion=json_each.value AND x.action='retract')`).bind(JSON.stringify(members)).all<{ id: string }>()).results.map(r => r.id) : members;
  if (!open.length) tools.fail(409, 'This claim was already retracted.');
  const ids = open.map(() => 'cf:' + crypto.randomUUID());
  const statements = open.map((one, i) => env.ATLAS.prepare('INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,?,?,?,?,?,?)')
    .bind(ids[i], key, one, action, actor, at, reason));
  const subject: Subject = found.crop != null ? { crop: found.crop } : { subject: found.subject };
  return { statements, keys: [[found.subject, found.predicate, found.scope, found.slot]] as Slot[], actions: ids, subject };
}

/** The response saved for submission `key`, or null; a reuse of its id with another request is refused. */
export async function savedSubmission(env: Env, key: string, signature: string, fail: LedgerTools['fail']) {
  return repeat(env, key, signature, fail);
}

/** Write a submission's rows, the resolution of their slots and the submission itself in one batch,
 *  and answer with the response saved, which names the claims the batch retracted (only the batch
 *  knows them). A batch that fails because the same submission landed meanwhile answers with that one. */
export async function commitPlan(env: Env, submission: { key: string; actor: string; signature: string; at: string },
  plan: Plan, response: Json, tools: LedgerTools): Promise<Json> {
  const statements = [...plan.statements, ...resolveSlots(env, [...new Map(plan.keys.map(k => [JSON.stringify(k), k])).values()]),
    env.ATLAS.prepare(`INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,json_set(?,'$.retracted',
      json((SELECT json_group_array(assertion) FROM assertion_actions WHERE submission=? AND action='retract'))),?)`)
      .bind(submission.key, submission.actor, submission.signature, JSON.stringify(response), submission.key, submission.at)];
  try { await env.ATLAS.batch(statements) } catch (error) {
    const again = await repeat(env, submission.key, submission.signature, tools.fail);
    if (again) return again;
    throw error;
  }
  return (await repeat(env, submission.key, submission.signature, tools.fail))!;
}

// A grapheme is named by its code points (`U+5F8C`, at most four), as the site's character keys are.
export const GRAPHEME_KEY = /^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6}){0,3}$/;
export const keyText = (key: string) => key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('');
export const headsQuery = () => 'SELECT text FROM graphemes WHERE text IN (SELECT value FROM json_each(?)) AND coalesce(head,id)=id';
/** The keys among `named` of graphemes that head their family: a `has_grapheme` claim names one. */
export async function headKeys(env: Env, named: string[]): Promise<string[]> {
  const keys = named.filter(key => GRAPHEME_KEY.test(key));
  if (!keys.length) return [];
  const heads = new Set((await env.ATLAS.prepare(headsQuery()).bind(JSON.stringify(keys.map(keyText))).all<{ text: string }>()).results.map(r => r.text));
  return keys.filter(key => heads.has(keyText(key)));
}

/** Refuse a write on a crop its reviewer saw at another version: it was cut again, or its box redrawn. */
export function sameVersion(crop: CropNow, seen: unknown, fail: LedgerTools['fail']) {
  if (!crop.version) fail(409, 'This crop has no image to make a claim about.');
  if (seen !== crop.version) fail(409, 'This crop was cut again. Reload it.');
}

/** Record one claim about a crop (several members make an alternative set) as `actor`. The claim names
 *  the evidence version its reviewer saw (`crop_version`), and one made after the crop was recut is
 *  refused. A new claim in a slot that takes one value retracts the actor's own earlier ones there. */
export async function writeClaim(env: Env, input: Json, actor: string, tools: LedgerTools) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical(input);
  const found = await repeat(env, key, signature, tools.fail);
  if (found) { const previous = await withCrop(env, found, tools.alias); return { ...answered(previous), current: await currentOf(env, subjectOf(previous)) }; }
  const predicate = tools.text(input.predicate, 64, 'predicate', true)!;
  const spec = PREDICATES[predicate] ?? tools.fail(422, `Unknown predicate ${predicate}.`);
  const scope = tools.text(input.scope ?? '', 128, 'scope') ?? '';
  const raw = input.claims;
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > MEMBERS_MAX) tools.fail(422, `A claim names 1–${MEMBERS_MAX} values.`);
  const members: Member[] = raw.map((m: Json) => ({ object: m?.object == null ? null : tools.text(m.object, 128, 'object', true),
    value: m?.value ?? null, confidence: m?.confidence ?? null, confidence_scheme: m?.confidence_scheme == null ? null : tools.text(m.confidence_scheme, 64, 'confidence scheme', true) }));
  // An object this route can name is a form or a word the site holds, or a grapheme that heads its family.
  const named = members.flatMap(m => m.object === null ? [] : [m.object]);
  const kinds: Record<string, string> = named.length ? Object.fromEntries((await env.ATLAS.prepare(`SELECT id,'form' AS kind FROM forms WHERE id IN (SELECT value FROM json_each(?1))
    UNION ALL SELECT id,'word' FROM words WHERE id IN (SELECT value FROM json_each(?1))`).bind(JSON.stringify(named)).all<{ id: string; kind: string }>()).results.map(r => [r.id, r.kind])) : {};
  for (const key of await headKeys(env, named)) kinds[key] = 'grapheme';
  const method = tools.text(input.method ?? null, 64, 'method');
  if (input.evidence != null && !Array.isArray(input.evidence)) tools.fail(422, 'Evidence is a list.');
  const evidence: Evidence[] = (input.evidence ?? []).map((e: Json) => ({ kind: tools.text(e?.kind, 16, 'evidence kind', true)!,
    ref: tools.text(e?.ref, 512, 'evidence reference', true)!, locator: tools.text(e?.locator ?? null, 512, 'evidence locator') }));
  checkClaims(predicate, members, kinds, tools.fail, method, evidence);
  if (spec.subject !== 'crop') tools.fail(422, `${predicate} has no subject the site can check yet.`);
  const crop = await tools.crop(env, tools.text(input.subject, 512, 'subject', true));
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString();
  const plan = await planClaim(env, { key, actor, crop, predicate, scope, members, at, method, evidence }, tools);
  const saved = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, cut: crop.cut, assertions: plan.assertions }, tools);
  return { ...answered(saved), current: await currentOf(env, { crop: crop.id }) };
}

/** Accept, reject, retract or adjudicate one claim as `actor` (`planAction`). */
export async function actOnClaim(env: Env, input: Json, target: string, actor: string, admin: boolean, tools: LedgerTools) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ target, input });
  const found = await repeat(env, key, signature, tools.fail);
  if (found) { const previous = await withCrop(env, found, tools.alias); return { ...answered(previous), current: await currentOf(env, subjectOf(previous)) }; }
  const at = new Date().toISOString();
  const plan = await planAction(env, { key, actor, target, action: tools.text(input.action, 16, 'action', true)!,
    reason: tools.text(input.reason ?? '', 500, 'reason') ?? '', admin, at }, tools);
  const saved = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, ...plan.subject, actions: plan.actions }, tools);
  return { ...answered(saved), current: await currentOf(env, plan.subject) };
}

// A subject's latest claims, oldest first, each with its evidence and the actions taken on it: one
// range of `assertion_crop` or `assertion_slot` read backwards, and each claim's rows by their own keys.
const CLAIM_PARTS = `(SELECT json_group_array(json_object('kind',e.kind,'ref',e.ref,'locator',e.locator,'crop',e.crop,'cut',e.cut)) FROM assertion_evidence e WHERE e.assertion=a.id) AS evidence,
  (SELECT json_group_array(json_object('assertion',p.premise,'role',p.role)) FROM assertion_premises p WHERE p.assertion=a.id) AS premises,
  (SELECT json_group_array(json_object('id',x.id,'action',x.action,'actor',x.actor,'at',x.at,'reason',x.reason)) FROM assertion_actions x WHERE x.assertion=a.id) AS actions`;
export const claimHistoryQuery = (by: 'crop' | 'subject') => `SELECT * FROM (SELECT a.*,${CLAIM_PARTS}
  FROM assertions a WHERE ${by === 'crop' ? 'a.crop=?' : 'a.subject=? AND a.crop IS NULL'} ORDER BY a.asserted_at DESC,a.id DESC LIMIT ${HISTORY_MAX})
  ORDER BY asserted_at,id`;
const parseClaim = (row: Json) => outRow({ ...row, value: row.value === null ? null : JSON.parse(row.value),
  evidence: outEvidence(JSON.parse(row.evidence)), premises: JSON.parse(row.premises), actions: JSON.parse(row.actions) });
/** A subject's claims and its current slots; a crop's subject is its public id. */
export async function claimsOf(env: Env, of: Subject) {
  const statement = 'crop' in of ? env.ATLAS.prepare(claimHistoryQuery('crop')).bind(of.crop) : env.ATLAS.prepare(claimHistoryQuery('subject')).bind(of.subject);
  const history = (await statement.all<Json>()).results.map(parseClaim);
  return { subject: 'crop' in of ? publicId(of.crop) : of.subject, resolver: RESOLVER, current: await currentOf(env, of), history };
}

// The ledger for `glyph_atlas.review.cloudflare_import`, a page at a time in the order it was written:
// claims after rowid `after`, actions after rowid `actions_after`, each with its evidence and premises.
const EXPORT_MAX = 500;
export const ledgerClaimsQuery = () => `SELECT a.rowid AS seq,a.*,
  (SELECT json_group_array(json_object('kind',e.kind,'ref',e.ref,'locator',e.locator,'crop',e.crop,'cut',e.cut)) FROM assertion_evidence e WHERE e.assertion=a.id) AS evidence,
  (SELECT json_group_array(json_object('premise',p.premise,'role',p.role)) FROM assertion_premises p WHERE p.assertion=a.id) AS premises
  FROM assertions a WHERE a.rowid>? ORDER BY a.rowid LIMIT ?`;
export const ledgerActionsQuery = () => 'SELECT rowid AS seq,* FROM assertion_actions WHERE rowid>? ORDER BY rowid LIMIT ?';
export async function ledgerPage(env: Env, q: URLSearchParams, integer: (q: URLSearchParams, key: string, fallback: number, max?: number) => number) {
  const after = integer(q, 'after', 0, Number.MAX_SAFE_INTEGER), actionsAfter = integer(q, 'actions_after', 0, Number.MAX_SAFE_INTEGER);
  const limit = Math.max(1, integer(q, 'limit', EXPORT_MAX, EXPORT_MAX));
  const [claims, actions] = await env.ATLAS.batch<Json>([env.ATLAS.prepare(ledgerClaimsQuery()).bind(after, limit),
    env.ATLAS.prepare(ledgerActionsQuery()).bind(actionsAfter, limit)]);
  const assertions: Json[] = claims.results.map(row => outRow({ ...row, evidence: outEvidence(JSON.parse(row.evidence)), premises: JSON.parse(row.premises) }));
  return { version: 1, kind: 'atlas-ledger', resolver: RESOLVER, assertions, actions: actions.results,
    next: { after: assertions.at(-1)?.seq ?? after, actions_after: actions.results.at(-1)?.seq ?? actionsAfter },
    done: assertions.length < limit && actions.results.length < limit };
}
