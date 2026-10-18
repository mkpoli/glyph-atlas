// The word a crop writes at its place in the text (decision 0004).
import { checkClaims, commitPlan, planAction, planClaim, PREDICATES, savedSubmission, submissionId, type LedgerTools, type Member } from './ledger';

type Json = Record<string, any>;
const spec = PREDICATES.writes_word;
const formOf = (confidence: number | null, scheme: string | null) => scheme === 'form'
  ? Object.entries(spec.confidence!.form).find(([, value]) => value === confidence)?.[0] ?? null : null;

/** Set the reader's word, or retract their own latest claim with `word: null`. */
export async function setWord(env: Env, input: Json, cropId: string, actor: string, tools: LedgerTools) {
  const id = submissionId(input, tools), key = actor + ':' + id, signature = tools.canonical({ crop: cropId, input });
  const saved = await savedSubmission(env, key, signature, tools.fail);
  if (saved) return { id: saved.subject, word: (await wordsFor(env, [saved.subject])).get(saved.subject) ?? null, replaced: saved.replaced ?? null };
  let member: Member | null = null;
  if (input.word !== null) {
    if (typeof input.word !== 'string' || !await env.DB.prepare('SELECT 1 FROM words WHERE id=?').bind(input.word).first()) tools.fail(422, 'No such word.');
    if (input.form !== 'certain' && input.form !== 'probable') tools.fail(422, 'Say whether the form is certain or probable.');
    const confidence = spec.confidence!.form[input.form];
    member = { object: input.word, value: null, confidence, confidence_scheme: 'form' };
    checkClaims('writes_word', [member], { [input.word]: 'word' }, tools.fail, input.method);
  }
  const own = await env.DB.prepare(`SELECT a.id,a.object,a.method,a.confidence,a.confidence_scheme FROM assertions a
    WHERE a.subject=? AND a.predicate='writes_word' AND a.scope='' AND a.slot='' AND (a.asserted_by=? OR a.asserted_by IN ${tools.owned(actor)})
    AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
    ORDER BY a.rowid DESC LIMIT 1`).bind(cropId, actor).first<{ id: string; object: string | null; method: string; confidence: number | null; confidence_scheme: string | null }>();
  if (!member && !own) tools.fail(409, 'You have no word on this crop to clear.');
  const crop = await tools.crop(env, cropId, input.crop_version);
  if (!crop.version) tools.fail(409, 'This crop has no image to make a claim about.');
  if (input.crop_version !== crop.version) tools.fail(409, 'This crop was cut again. Reload it.');
  const at = new Date().toISOString();
  const plan = member ? await planClaim(env, { key, actor, subject: crop.id, predicate: 'writes_word', scope: '',
    members: [member], version: crop.version, at, method: input.method }, tools)
    : await planAction(env, { key, actor, target: own!.id, action: 'retract', reason: 'cleared', admin: false, at }, tools);
  const replaced = own ? { word: own.object, method: own.method, form: formOf(own.confidence, own.confidence_scheme) } : null;
  const result = await commitPlan(env, { key, actor, signature, at }, plan, { submission: key, subject: crop.id, replaced }, tools);
  return { id: crop.id, word: (await wordsFor(env, [crop.id])).get(crop.id) ?? null, replaced: result.replaced ?? null };
}

export const cropWordsQuery = () => `SELECT c.subject,c.status,c.members,c.claims,c.supporting FROM current_claims c
  JOIN units u ON u.id=c.subject WHERE c.subject IN (SELECT value FROM json_each(?)) AND c.predicate='writes_word' AND c.scope='' AND c.slot=''
  AND c.crop_version IS u.crop_version`;
export const wordNamesQuery = () => 'SELECT id,reading,class FROM words WHERE id IN (SELECT value FROM json_each(?))';
export const wordMethodsQuery = () => 'SELECT id,method FROM assertions WHERE id IN (SELECT value FROM json_each(?))';
const PAGE_MAX = 200;

/** The word slot on each crop's current cut, including competing readings when disputed. */
export async function wordsFor(env: Env, ids: string[]): Promise<Map<string, Json>> {
  const found = new Map<string, Json>(), wanted = [...new Set(ids)].slice(0, PAGE_MAX);
  if (!wanted.length) return found;
  const rows = (await env.DB.prepare(cropWordsQuery()).bind(JSON.stringify(wanted)).all<Json>()).results;
  if (!rows.length) return found;
  const parsed: Json[] = rows.map(row => ({ ...row, members: JSON.parse(row.members), claims: JSON.parse(row.claims), supporting: JSON.parse(row.supporting) }));
  const valuesOf = (row: Json): Json[] => row.members.length ? row.members
    : row.status === 'disputed' ? row.claims.filter((c: Json) => c.standing).flatMap((c: Json) => c.members) : [];
  const words = [...new Set(parsed.flatMap(row => valuesOf(row).flatMap(m => m.object ? [m.object] : [])))];
  const supporting = [...new Set(parsed.flatMap(row => row.supporting))];
  const names = new Map(words.length ? (await env.DB.prepare(wordNamesQuery()).bind(JSON.stringify(words)).all<{ id: string; reading: string; class: string }>())
    .results.map(r => [r.id, { reading: r.reading, class: r.class }]) : []);
  const methods = new Map(supporting.length ? (await env.DB.prepare(wordMethodsQuery()).bind(JSON.stringify(supporting)).all<{ id: string; method: string }>())
    .results.map(r => [r.id, r.method]) : []);
  for (const row of parsed) {
    const values = valuesOf(row).map(m => ({ ...(m.object ? { word: m.object, ...(names.get(m.object) ?? { reading: null, class: null }) } : { gloss: m.value.gloss }),
      form: formOf(m.confidence, m.scheme) }));
    const by = [...new Set((row.claims as Json[]).filter(c => row.status === 'disputed' ? c.standing : c.members.some((m: Json) => row.supporting.includes(m.assertion)))
      .map(c => c.asserted_by))];
    const held = new Set(row.supporting.map((id: string) => methods.get(id)));
    found.set(row.subject, { status: row.status, values, methods: spec.methods!.filter(method => held.has(method)), supporting: row.supporting, by });
  }
  return found;
}

export async function withWords<T extends Json>(env: Env, items: T[]): Promise<(T & { word: Json | null })[]> {
  const words = await wordsFor(env, items.map(item => item.id));
  return items.map(item => ({ ...item, word: words.get(item.id) ?? null }));
}

// Spellings find their words by the spelling index; reading searches use a range of the word key.
export const wordChoicesQuery = (reading = false) => `SELECT id,reading,class FROM words WHERE ${reading
  ? 'id>=? AND id<?' : 'id IN (SELECT word FROM word_spellings WHERE spelling IN (SELECT value FROM json_each(?)))'} ORDER BY id LIMIT 20`;
export const wordSpellingsQuery = () => `SELECT DISTINCT word,spelling FROM word_spellings
  WHERE word IN (SELECT value FROM json_each(?)) ORDER BY word,spelling`;

/** Words spelled by the crop's characters first, followed by matches for the reading prefix. */
export async function wordChoices(env: Env, q: string, chars: string[]) {
  const wanted = [...new Set(chars)].slice(0, 4);
  const spelled = wanted.length ? (await env.DB.prepare(wordChoicesQuery()).bind(JSON.stringify(wanted)).all<Json>()).results : [];
  const reading = q.trim().replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));
  const prefix = [...'ja/' + reading];
  while (prefix.at(-1) === '\u{10ffff}') prefix.pop();
  const last = prefix.pop()!;
  const matches = reading && spelled.length < 20 ? (await env.DB.prepare(wordChoicesQuery(true))
    .bind('ja/' + reading, prefix.join('') + String.fromCodePoint(last.codePointAt(0)! + 1)).all<Json>()).results : [];
  const choices = [...new Map([...spelled, ...matches].map(row => [row.id, row])).values()].slice(0, 20);
  const spellings = choices.length ? (await env.DB.prepare(wordSpellingsQuery()).bind(JSON.stringify(choices.map(row => row.id))).all<{ word: string; spelling: string }>()).results : [];
  return choices.map(row => ({ ...row, spellings: spellings.filter(s => s.word === row.id).map(s => s.spelling) }));
}
