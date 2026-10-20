// The claim ledger's routes on glyph-atlas-v3 (decision record 0006): claims and actions, a crop's
// form and word as the pickers set them, the ledger's reads and export, and a book's dates with the
// claims they rest on. Crops are named by public id at this boundary and by integer id inside.
import { bundle, pageOf, publicId, slotOf as cropSlot } from '../atlas3';
import { StaleCrop, cropQueryParam, movedQueryResponse, staleResponse, writtenCropField } from '../atlas3Crops';
import { formSlot } from '../cropForms';
import { formOf, slotBacking, wordSlot } from '../cropWords';
import { axisOf } from '../dating';
import { actOnClaim, checkClaims, claimsOf, commitPlan, type CropNow, ledgerPage, type LedgerTools, type Member, ownedActors, planAction, planClaim,
  PREDICATES, type Plan, sameVersion, savedSubmission, submissionId, writeClaim } from '../ledger';
import { readRecords, recordKey } from '../records';
import { anchoredForm, representationId, typed, type FormProblem } from '../representation';

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
// lookups of `crops`, `pages` and `reviews`.
export const cropNowQuery = () => `SELECT c.cut,c.kind,p.rev,r.x,r.y,r.w,r.h FROM crops c JOIN pages p ON p.id=c.id>>24
  LEFT JOIN reviews r ON r.crop=c.id WHERE c.id=?`;
/** A crop's cut, its evidence version as its record shows it (`<public id>@<pixels>@<x,y,w,h>`, with
 *  the redrawn box while one waits), its text id and its kind; null for a crop with no row or entry. */
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
  const values = box == null ? null : ['x', 'y', 'w', 'h'].map(key => box[key]);
  const version = typeof pixels !== 'string' ? null : values === null ? `${publicId(id)}@${pixels}@`
    : values.every(Number.isSafeInteger) ? `${publicId(id)}@${pixels}@${values.join(',')}` : null;
  return { id, cut: row.cut, version, text: entry.id, kind: row.kind };
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

const ledgerTools = (tools: ClaimTools, ctx?: ExecutionContext): LedgerTools => ({ fail: tools.fail, text: tools.text, canonical: tools.canonical,
  owned: ownedActors, crop: (env, named) => namedCrop(env, named, tools.fail, ctx) });

// A crop's slot that holds on its cut now: one key of `current_claims` per crop, by `current_claim_crop`.
export const cropSlotsQuery = (predicate: 'has_form' | 'writes_word') => `SELECT c.crop,c.status,c.object,c.value,c.members,c.claims,c.supporting
  FROM json_each(?) j JOIN current_claims c ON c.crop=j.value AND c.predicate='${predicate}' JOIN crops k ON k.id=c.crop
  WHERE c.scope='' AND c.slot='' AND c.cut=k.cut`;
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
export const ownClaimQuery = (predicate: 'has_form' | 'writes_word', owned: string) => `SELECT a.id,a.object,a.method,a.confidence,a.confidence_scheme,
  ${predicate === 'has_form' ? '(SELECT r.value FROM forms f JOIN representations r ON r.id=f.anchor WHERE f.id=a.object)' : 'NULL'} AS text
  FROM assertions a WHERE a.crop=? AND a.predicate='${predicate}' AND a.scope='' AND a.slot='' AND (a.asserted_by=? OR a.asserted_by IN ${owned})
  AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract') ORDER BY a.rowid DESC LIMIT 1`;
type Own = { id: string; object: string | null; method: string; confidence: number | null; confidence_scheme: string | null; text: string | null };

/** Set (or with `form` null, clear) `actor`'s form for one crop, and answer with the crop's form and
 *  `replaced`: the value of the actor's own claim the save took back, or null, so a client can put it back.
 *  A value the reviewer picks or types names a representation and that value's broad form, which the
 *  first such choice creates together with the claim that the representation names it. */
export async function setForm(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(tools, ctx);
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: publicId(saved.crop), form: await formOfCrop(env, saved.crop), replaced: saved.replaced ?? null };
  if (input.review != null && !(typeof input.review === 'string' && /^[0-9a-f-]{36}$/i.test(input.review))) tools.fail(422, 'Invalid review id.');
  const typedForm = input.form == null ? null : tools.text(input.form, 256, 'form');
  const representation = typedForm ? typed(tools.literal(typedForm)) : null;
  if (typeof representation === 'string') tools.fail(422, PROBLEMS[representation]);
  const crop = await ledger.crop(env, named);
  const own = await env.ATLAS.prepare(ownClaimQuery('has_form', ownedActors(actor))).bind(crop.id, actor).first<Own>();
  if (!representation && !own) tools.fail(409, 'You have no form on this crop to clear.');
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString(), plan: Plan = { statements: [], keys: [] };
  if (representation) {
    const rep = await representationId(representation), form = await anchoredForm(rep);
    plan.statements.push(env.ATLAS.prepare('INSERT OR IGNORE INTO representations(id,scheme,value,namespace,version) VALUES(?,?,?,?,?)')
      .bind(rep, representation.scheme, representation.value, representation.namespace, representation.version));
    // The first choice of a value names its form: the form and the claim that the value names it,
    // under an id derived from the form, so two first choices made at once name it once.
    if (!await env.ATLAS.prepare('SELECT 1 FROM forms WHERE id=?').bind(form).first()) {
      plan.statements.push(env.ATLAS.prepare('INSERT OR IGNORE INTO forms(id,anchor,created_by,created_at) VALUES(?,?,?,?)').bind(form, rep, actor, at),
        env.ATLAS.prepare(`INSERT OR IGNORE INTO assertions(id,submission,subject,predicate,scope,slot,object,tier,asserted_by,asserted_at,method)
          VALUES(?,?,?,'represented_by','',?,?,'observed',?,?,'form-picker')`).bind(await namingId(form), key, form, rep, rep, actor, at));
      plan.keys.push([form, 'represented_by', '', rep]);
    }
    const claim = await planClaim(env, { key, actor, crop, predicate: 'has_form', scope: '', at, method: 'form-picker',
      members: [{ object: form, value: null, confidence: null, confidence_scheme: null }] }, ledger);
    plan.statements.push(...claim.statements); plan.keys.push(...claim.keys);
  } else {
    const cleared = await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger);
    plan.statements.push(...cleared.statements); plan.keys.push(...cleared.keys);
  }
  const replaced = own?.text ?? null;
  await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, replaced }, ledger);
  return { id: publicId(crop.id), form: await formOfCrop(env, crop.id), replaced };
}

// The id of the claim that names a form, in the shape of the site's other ids and the same for every
// first choice of its value.
async function namingId(form: string) {
  const hex = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('named\n' + form)))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  return `cf:${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/** Set the reader's word for one crop (decision 0004), or retract their own latest claim with `word: null`. */
export async function setWord(env: Env, input: Json, named: string, actor: string, tools: ClaimTools, ctx?: ExecutionContext) {
  const ledger = ledgerTools(tools, ctx), spec = PREDICATES.writes_word;
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: named, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: publicId(saved.crop), word: await wordOfCrop(env, saved.crop), replaced: saved.replaced ?? null };
  let member: Member | null = null;
  if (input.word !== null) {
    if (typeof input.word !== 'string' || !await env.ATLAS.prepare('SELECT 1 FROM words WHERE id=?').bind(input.word).first()) tools.fail(422, 'No such word.');
    if (input.form !== 'certain' && input.form !== 'probable') tools.fail(422, 'Say whether the form is certain or probable.');
    member = { object: input.word, value: null, confidence: spec.confidence!.form[input.form], confidence_scheme: 'form' };
    checkClaims('writes_word', [member], { [input.word]: 'word' }, tools.fail, input.method);
  }
  const crop = await ledger.crop(env, named);
  const own = await env.ATLAS.prepare(ownClaimQuery('writes_word', ownedActors(actor))).bind(crop.id, actor).first<Own>();
  if (!member && !own) tools.fail(409, 'You have no word on this crop to clear.');
  sameVersion(crop, input.crop_version, tools.fail);
  const at = new Date().toISOString();
  const plan = member ? await planClaim(env, { key, actor, crop, predicate: 'writes_word', scope: '', members: [member], at, method: input.method }, ledger)
    : await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, ledger);
  const replaced = own ? { word: own.object, method: own.method, form: formOf(own.confidence, own.confidence_scheme) } : null;
  const result = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, crop: crop.id, replaced }, ledger);
  return { id: publicId(crop.id), word: await wordOfCrop(env, crop.id), replaced: result.replaced ?? null };
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

/** A write to the ledger: a claim, an action on one, or a crop's form or word. `null` for another path. */
export async function claimWrite(env: Env, request: Request, path: string, me: { id: string; admin: boolean }, tools: ClaimTools,
  limit: () => Promise<void>, ctx?: ExecutionContext): Promise<Json | Response | null> {
  const acted = path.match(/^\/atlas\/claims\/([^/]+)\/actions$/), picked = path.match(/^\/atlas\/characters\/([^/]+)\/(form|word)$/);
  if (path !== '/atlas/claims' && !acted && !picked) return null;
  // A claim, an action or a crop's form is a few small rows, and one address is held to a rate.
  await limit();
  const input = await tools.body(request);
  let target = '';
  try { target = decodeURIComponent((acted ?? picked)?.[1] ?? '') } catch { tools.fail(404, acted ? 'No such claim.' : NOT_HERE) }
  try {
    if (picked) return picked[2] === 'word' ? await setWord(env, input, target, me.id, tools, ctx) : await setForm(env, input, target, me.id, tools, ctx);
    const ledger = ledgerTools(tools, ctx);
    return acted ? await actOnClaim(env, input, target, me.id, me.admin, ledger) : await writeClaim(env, input, me.id, ledger);
  } catch (error) {
    if (error instanceof StaleCrop) return staleResponse();
    throw error;
  }
}

/** A read of the ledger: a subject's claims, or the export a page at a time. `null` for another path. */
export async function claimRead(env: Env, url: URL, tools: ClaimTools, ctx?: ExecutionContext): Promise<Json | Response | null> {
  const path = url.pathname, q = url.searchParams;
  if (path === '/atlas/ledger' || path === '/atlas/ledger.json') {
    const page = await ledgerPage(env, q, tools.integer);
    return Response.json(page, { headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff',
      ...(path.endsWith('.json') ? { 'content-disposition': 'attachment; filename="atlas-ledger.json"' } : {}) } });
  }
  if (path !== '/atlas/claims') return null;
  const subject = tools.text(q.get('subject'), 512, 'subject', true)!;
  // A crop is named by public id; its old text id moves there. Any other subject is a key of its own.
  const named = await cropQueryParam(env, subject, ctx);
  if (named && 'moved' in named) return movedQueryResponse(url, 'subject', named.moved);
  if (named) return claimsOf(env, { crop: 'id' in named ? named.id : named.retired });
  return claimsOf(env, { subject });
}
