// The assertion ledger on the site (migrations 0048, 0049; docs/design/form-model.md). A claim and each
// action on it are rows written in one D1 batch with the resolution of their slot, so `current_claims`
// always holds what the ledger says. The catalogue and the resolver are `data/ledger.json`, which the
// review service and the publication read too: the three resolve a slot with the same SQL.
import catalogue from '../../../data/ledger.json';

type Json = Record<string, any>;
type Predicate = { label: string; subject: string; objects: string[]; values?: string[]; cardinality: 'one' | 'many'; alternatives?: boolean };
export const RESOLVER: string = catalogue.resolver;
export const PREDICATES: Record<string, Predicate> = catalogue.predicates as Record<string, Predicate>;
const ACTIONS = new Set<string>(catalogue.actions);
const COLUMNS = 'subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop_version,resolver,at';
const BODY = (catalogue.resolve as string[]).filter(line => !line.trimStart().startsWith('--')).join('\n');
// `crop_now` from D1's own crops: a crop subject's current version, `units.crop_version` (0047).
const CROP_NOW = "SELECT id,crop_version FROM units WHERE id IN (SELECT json_extract(value,'$[0]') FROM json_each(?1))";
// By key: the slots are rows of `current_claims`' primary key, each found by it.
export const resolveClearQuery = () => `DELETE FROM current_claims WHERE (subject,predicate,scope,slot) IN
  (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?1))`;
export const resolveWriteQuery = () => `INSERT INTO current_claims(${COLUMNS}) WITH crop_now(unit,version) AS (${CROP_NOW}),\n${BODY}`;
// At most this many members in one alternative set, and this many claims and actions in a subject's history.
const MEMBERS_MAX = 8, HISTORY_MAX = 200;

export type LedgerTools = {
  fail: (status: number, message: string) => never;
  body: (request: Request) => Promise<Json>;
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  canonical: (value: unknown) => string;
  // A crop subject as the site holds it, and its current evidence version. A corpus glyph nothing has
  // named yet gets its `units` row when the client saw the version that row will have (`expected`).
  crop: (env: Env, id: string, expected: unknown) => Promise<{ id: string; version: string | null }>;
  // The actor ids one user holds (`actors`), as SQL.
  owned: (user: string) => string;
};
export type Member = { object: string | null; value: unknown; confidence: number | null; confidence_scheme: string | null };

/** Refuse members the catalogue does not allow; `kinds` gives the kind of each named entity. */
export function checkClaims(predicate: string, members: Member[], kinds: Record<string, string>, fail: LedgerTools['fail']) {
  const spec = PREDICATES[predicate];
  if (!spec) fail(422, `Unknown predicate ${predicate}.`);
  if (!members.length) fail(422, 'A claim names a value.');
  if (members.length > 1 && !(spec.alternatives && spec.cardinality === 'one')) fail(422, `${predicate} takes one value a claim.`);
  const seen = new Set<string>();
  for (const m of members) {
    if ((m.object === null) === (m.value === null)) fail(422, 'A claim names an object or a value.');
    if (m.object !== null && !spec.objects.includes(kinds[m.object])) fail(422, `${m.object} cannot be the object of ${predicate}.`);
    if (m.object === null && !(spec.values ?? []).includes(m.value as string)) fail(422, `${JSON.stringify(m.value)} is not a value of ${predicate}.`);
    if ((m.confidence === null) !== (m.confidence_scheme === null)) fail(422, 'A confidence names its scheme.');
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
  return [env.DB.prepare(resolveClearQuery()).bind(bound), env.DB.prepare(resolveWriteQuery()).bind(bound)];
}
const parseRow = (row: Json) => ({ ...row, value: row.value === null ? null : JSON.parse(row.value),
  members: JSON.parse(row.members), supporting: JSON.parse(row.supporting), claims: JSON.parse(row.claims) });
// A subject's resolved slots that hold on its current version; a crop's rows resolved on an earlier cut do not.
export const currentClaimsQuery = () => `SELECT c.* FROM current_claims c LEFT JOIN units u ON u.id=c.subject
  WHERE c.subject=? AND c.crop_version IS u.crop_version ORDER BY c.predicate,c.scope,c.slot`;
async function currentOf(env: Env, subject: string) {
  return (await env.DB.prepare(currentClaimsQuery()).bind(subject).all<Json>()).results.map(parseRow);
}
async function repeat(env: Env, key: string, signature: string, fail: LedgerTools['fail']) {
  const saved = await env.DB.prepare('SELECT request,response FROM ledger_submissions WHERE id=?').bind(key).first<{ request: string; response: string }>();
  if (saved && saved.request !== signature) fail(409, 'This submission was already saved with different values.');
  return saved ? JSON.parse(saved.response) as Json : null;
}
function submissionId(input: Json, tools: LedgerTools) {
  const id = tools.text(input.id, 64, 'submission id', true)!;
  if (!/^[0-9a-f-]{36}$/i.test(id)) tools.fail(422, 'Invalid submission id.');
  return id;
}

// A random id in the shape of a UUID, made in SQL for rows a batch writes by selecting them.
const SQL_UUID = `lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(6)))`;
/** The statement that retracts `actor`'s earlier live claims in a single-valued slot, run in the same
 *  batch after their new claim (`by`), so a concurrent claim of theirs is retracted too. */
export function retractOwn(env: Env, own: { key: string; actor: string; subject: string; predicate: string; scope: string; at: string; by: string }) {
  return env.DB.prepare(`INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason)
    SELECT 'cf:'||${SQL_UUID},?,a.id,'retract',?,?,? FROM assertions a WHERE a.subject=? AND a.predicate=? AND a.scope=? AND a.slot=''
    AND a.asserted_by=? AND a.submission IS NOT ? AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')`)
    .bind(own.key, own.actor, own.at, 'superseded by ' + own.by, own.subject, own.predicate, own.scope, own.actor, own.key);
}

/** Record one claim about a subject (several members make an alternative set) as `actor`. A crop
 *  subject's claim names the evidence version its reviewer saw, and one made after the crop was recut
 *  is refused. A new claim in a slot that takes one value retracts the actor's own earlier ones there.
 *  `extra` writes rows a claim needs first (a form it names), in the same batch. */
export async function writeClaim(env: Env, input: Json, actor: string, tools: LedgerTools,
  extra: { statements?: D1PreparedStatement[]; kinds?: Record<string, string> } = {}) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical(input);
  const previous = await repeat(env, key, signature, tools.fail);
  if (previous) return { ...previous, current: await currentOf(env, previous.subject) };
  const predicate = tools.text(input.predicate, 64, 'predicate', true)!;
  const spec = PREDICATES[predicate] ?? tools.fail(422, `Unknown predicate ${predicate}.`);
  const scope = tools.text(input.scope ?? '', 128, 'scope') ?? '';
  const raw = input.claims;
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > MEMBERS_MAX) tools.fail(422, `A claim names 1–${MEMBERS_MAX} values.`);
  const members: Member[] = raw.map((m: Json) => ({ object: m?.object == null ? null : tools.text(m.object, 128, 'object', true),
    value: m?.value ?? null, confidence: m?.confidence ?? null, confidence_scheme: m?.confidence_scheme == null ? null : tools.text(m.confidence_scheme, 64, 'confidence scheme', true) }));
  checkClaims(predicate, members, extra.kinds ?? {}, tools.fail);
  let subject = tools.text(input.subject, 512, 'subject', true)!, version: string | null = null;
  const statements: D1PreparedStatement[] = [];
  if (spec.subject === 'crop') {
    const crop = await tools.crop(env, subject, input.crop_version);
    subject = crop.id, version = crop.version;
    if (!version) tools.fail(409, 'This crop has no image to make a claim about.');
    if (input.crop_version !== version) tools.fail(409, 'This crop was cut again. Reload it.');
  } else tools.fail(422, `${predicate} has no subject the site can check yet.`);
  statements.push(...extra.statements ?? []);
  const at = new Date().toISOString(), set = members.length > 1 ? 'cf:' + crypto.randomUUID() : null;
  const ids = members.map(() => 'cf:' + crypto.randomUUID());
  members.forEach((m, i) => {
    statements.push(env.DB.prepare(`INSERT INTO assertions(id,submission,subject,predicate,scope,slot,object,value,alternative_set,tier,asserted_by,asserted_at,confidence,confidence_scheme,method)
      VALUES(?,?,?,?,?,?,?,?,?,'observed',?,?,?,?,?)`).bind(ids[i], key, subject, predicate, scope, slotOf(predicate, m, tools.canonical), m.object,
      m.value === null ? null : tools.canonical(m.value), set, actor, at, m.confidence, m.confidence_scheme, tools.text(input.method ?? null, 64, 'method')));
    if (version) statements.push(env.DB.prepare("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES(?,'crop',?)").bind(ids[i], version));
  });
  // The actor's own earlier claims in a slot that takes one value are retracted by the batch itself,
  // so a claim of theirs saved meanwhile is retracted too.
  if (spec.cardinality === 'one') statements.push(retractOwn(env, { key, actor, subject, predicate, scope, at, by: ids[0] }));
  const slots = [...new Set(members.map(m => slotOf(predicate, m, tools.canonical)))];
  statements.push(...resolveSlots(env, slots.map(slot => [subject, predicate, scope, slot])));
  // The response names what the batch retracted, which only the batch knows.
  statements.push(env.DB.prepare(`INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,json_object('submission',?,'subject',?,
    'assertions',json(?),'retracted',json((SELECT json_group_array(assertion) FROM assertion_actions WHERE submission=? AND action='retract'))),?)`)
    .bind(key, actor, signature, key, subject, JSON.stringify(ids), key, at));
  try { await env.DB.batch(statements) } catch (error) {
    const again = await repeat(env, key, signature, tools.fail);
    if (again) return { ...again, current: await currentOf(env, again.subject) };
    throw error;
  }
  const saved = (await repeat(env, key, signature, tools.fail))!;
  return { ...saved, current: await currentOf(env, subject) };
}

/** Accept, reject, retract or adjudicate one claim as `actor`. A retraction is the asserter's, an accept
 *  or a reject anyone else's, an adjudication an admin's; retracting one alternative retracts its set. */
export async function actOnClaim(env: Env, input: Json, target: string, actor: string, admin: boolean, tools: LedgerTools) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ target, input });
  const previous = await repeat(env, key, signature, tools.fail);
  if (previous) return { ...previous, current: await currentOf(env, previous.subject) };
  const action = tools.text(input.action, 16, 'action', true)!;
  if (!ACTIONS.has(action)) tools.fail(422, 'Unknown action.');
  const reason = tools.text(input.reason ?? '', 500, 'reason') ?? '';
  const found = await env.DB.prepare(`SELECT a.*,a.asserted_by IN ${tools.owned(actor)} OR a.asserted_by=? AS mine FROM assertions a WHERE a.id=?`)
    .bind(actor, target).first<Json>() ?? tools.fail(404, 'No such claim.');
  if (action === 'retract' && !found.mine) tools.fail(403, 'Only the person who made a claim can retract it.');
  if ((action === 'accept' || action === 'reject') && found.mine) tools.fail(422, 'A claim is already its asserter\'s own; retract it instead.');
  if (action === 'adjudicate' && !admin) tools.fail(403, 'Only an adjudicator can decide between claims.');
  const members = action === 'retract' && found.alternative_set
    ? (await env.DB.prepare('SELECT id FROM assertions WHERE alternative_set=? ORDER BY rowid').bind(found.alternative_set).all<{ id: string }>()).results.map(r => r.id)
    : [target];
  const open = action === 'retract' ? (await env.DB.prepare(`SELECT value AS id FROM json_each(?) WHERE NOT EXISTS
    (SELECT 1 FROM assertion_actions x WHERE x.assertion=json_each.value AND x.action='retract')`).bind(JSON.stringify(members)).all<{ id: string }>()).results.map(r => r.id) : members;
  if (!open.length) tools.fail(409, 'This claim was already retracted.');
  const at = new Date().toISOString(), ids = open.map(() => 'cf:' + crypto.randomUUID());
  const statements = open.map((one, i) => env.DB.prepare('INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,?,?,?,?,?,?)')
    .bind(ids[i], key, one, action, actor, at, reason));
  statements.push(...resolveSlots(env, [[found.subject, found.predicate, found.scope, found.slot]]));
  const response = { submission: key, subject: found.subject, actions: ids };
  statements.push(env.DB.prepare('INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,?,?,?,?)').bind(key, actor, signature, JSON.stringify(response), at));
  try { await env.DB.batch(statements) } catch (error) {
    const again = await repeat(env, key, signature, tools.fail);
    if (again) return { ...again, current: await currentOf(env, again.subject) };
    throw error;
  }
  return { ...response, current: await currentOf(env, found.subject) };
}

// A subject's latest claims, oldest first, each with its evidence and the actions taken on it: one
// range of `assertion_subject` read backwards, and each claim's rows by their own keys.
export const claimHistoryQuery = () => `SELECT * FROM (SELECT a.*,
  (SELECT json_group_array(json_object('kind',e.kind,'ref',e.ref,'locator',e.locator)) FROM assertion_evidence e WHERE e.assertion=a.id) AS evidence,
  (SELECT json_group_array(json_object('assertion',p.premise,'role',p.role)) FROM assertion_premises p WHERE p.assertion=a.id) AS premises,
  (SELECT json_group_array(json_object('id',x.id,'action',x.action,'actor',x.actor,'at',x.at,'reason',x.reason)) FROM assertion_actions x WHERE x.assertion=a.id) AS actions
  FROM assertions a WHERE a.subject=? ORDER BY a.asserted_at DESC,a.id DESC LIMIT ${HISTORY_MAX}) ORDER BY asserted_at,id`;
const parseClaim = (row: Json) => ({ ...row, value: row.value === null ? null : JSON.parse(row.value),
  evidence: JSON.parse(row.evidence), premises: JSON.parse(row.premises), actions: JSON.parse(row.actions) });
export async function claimsOf(env: Env, subject: string) {
  const history = (await env.DB.prepare(claimHistoryQuery()).bind(subject).all<Json>()).results.map(parseClaim);
  return { subject, resolver: RESOLVER, current: await currentOf(env, subject), history };
}

// The ledger for `glyph_atlas.review.cloudflare_import`, a page at a time in the order it was written:
// claims after rowid `after`, actions after rowid `actions_after`, each with its evidence and premises.
const EXPORT_MAX = 500;
export const ledgerClaimsQuery = () => `SELECT a.rowid AS seq,a.*,
  (SELECT json_group_array(json_object('kind',e.kind,'ref',e.ref,'locator',e.locator)) FROM assertion_evidence e WHERE e.assertion=a.id) AS evidence,
  (SELECT json_group_array(json_object('premise',p.premise,'role',p.role)) FROM assertion_premises p WHERE p.assertion=a.id) AS premises
  FROM assertions a WHERE a.rowid>? ORDER BY a.rowid LIMIT ?`;
export const ledgerActionsQuery = () => 'SELECT rowid AS seq,* FROM assertion_actions WHERE rowid>? ORDER BY rowid LIMIT ?';
export async function ledgerPage(env: Env, q: URLSearchParams, integer: (q: URLSearchParams, key: string, fallback: number, max?: number) => number) {
  const after = integer(q, 'after', 0, Number.MAX_SAFE_INTEGER), actionsAfter = integer(q, 'actions_after', 0, Number.MAX_SAFE_INTEGER);
  const limit = Math.max(1, integer(q, 'limit', EXPORT_MAX, EXPORT_MAX));
  const [claims, actions] = await env.DB.batch<Json>([env.DB.prepare(ledgerClaimsQuery()).bind(after, limit),
    env.DB.prepare(ledgerActionsQuery()).bind(actionsAfter, limit)]);
  const assertions: Json[] = claims.results.map(row => ({ ...row, evidence: JSON.parse(row.evidence), premises: JSON.parse(row.premises) }));
  return { version: 1, kind: 'atlas-ledger', resolver: RESOLVER, assertions, actions: actions.results,
    next: { after: assertions.at(-1)?.seq ?? after, actions_after: actions.results.at(-1)?.seq ?? actionsAfter },
    done: assertions.length < limit && actions.results.length < limit };
}
