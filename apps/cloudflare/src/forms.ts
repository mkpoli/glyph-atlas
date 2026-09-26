// Form assignment on the hosted site: CODH shape clusters, and the forms people name for them.
// The clustering is published by scripts/export_forms_cloudflare.py; decisions are recorded here and
// applied to the same rows in one D1 batch, so `form_units.form` always holds a glyph's current form
// and `corpus_units.character` follows it for search and counts.
type Json = Record<string, any>;
export type FormTools = {
  fail: (status: number, message: string) => never;
  body: (request: Request) => Promise<Json>;
  text: (value: unknown, max: number, name: string, required?: boolean) => string | null;
  codePoints: (value: string) => string;
  family: (env: Env, char: string) => Promise<string>;
};
type UnitForm = { id: string; cluster: string; form: string | null; glyph_set: number; cluster_form: string | null;
  issue: string | null; issue_character: string | null; issue_family: string | null };

// A glyph decision names at most this many glyphs; the view sends larger selections in parts.
export const GLYPHS_PER_DECISION = 1000;

function count(q: URLSearchParams, key: string, fallback: number, max: number, tools: FormTools) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) tools.fail(422, `Invalid ${key}.`);
  return n;
}
const basis = (row: { glyph_set: number; form: string | null; issue?: string | null }) =>
  row.glyph_set ? 'form_glyph' : row.form || row.issue ? 'form_cluster' : null;
// What a cluster decision can say instead of a form: its glyphs differ, or none is this family's
// character, or none is cropped as one glyph. The last two are reported on each glyph.
const CLUSTER_ISSUES = ['mixed', 'character', 'crop'];
// A glyph's form and report are its own decision's when it has one, else its cluster's.
const SETTLE = `form=CASE WHEN glyph_set=1 THEN glyph_form ELSE cluster_form END,
  issue=CASE WHEN glyph_set=1 THEN glyph_issue ELSE cluster_issue END,
  issue_character=CASE WHEN glyph_set=1 THEN glyph_character ELSE cluster_character END,
  issue_family=CASE WHEN glyph_set=1 THEN glyph_family ELSE cluster_family END`;

async function families(env: Env) {
  const rows = await env.DB.prepare('SELECT code_point,char,label,count,cluster_count,assigned,rejected,revision FROM form_families ORDER BY count DESC,code_point')
    .all<Json>();
  if (!rows.results.length) return null;
  return { revision: rows.results[0].revision, items: rows.results.map(r => ({ code_point: r.code_point, char: r.char,
    label: r.label, count: r.count, clusters: r.cluster_count, assigned: r.assigned, rejected: r.rejected })) };
}

async function family(env: Env, codePoint: string, q: URLSearchParams, tools: FormTools) {
  const found = await env.DB.prepare('SELECT * FROM form_families WHERE code_point=?').bind(codePoint).first<Json>();
  if (!found) tools.fail(404, 'This family was not clustered.');
  const order = q.get('order') === 'size' ? 'size' : 'shape';
  const [clusters, tallies] = await env.DB.batch([
    env.DB.prepare(`SELECT * FROM form_clusters WHERE family=? ORDER BY ${order === 'size' ? 'size_position' : 'shape_position'}`).bind(codePoint),
    env.DB.prepare('SELECT cluster,form,count(*) AS n,sum(glyph_set) AS own,count(issue) AS rejected FROM form_units WHERE family=? GROUP BY cluster,form').bind(codePoint),
  ]);
  const byCluster = new Map<string, { assigned: number; own: number; rejected: number; forms: Map<string, number> }>();
  for (const t of tallies.results as Json[]) {
    const entry = byCluster.get(t.cluster) ?? { assigned: 0, own: 0, rejected: 0, forms: new Map() };
    entry.own += t.own; entry.rejected += t.rejected;
    if (t.form) { entry.assigned += t.n; entry.forms.set(t.form, (entry.forms.get(t.form) ?? 0) + t.n) }
    byCluster.set(t.cluster, entry);
  }
  return { revision: found!.revision, code_point: found!.code_point, char: found!.char, label: found!.label,
    count: found!.count, clusters: found!.cluster_count, assigned: found!.assigned, rejected: found!.rejected, order,
    forms: JSON.parse(found!.forms),
    items: (clusters.results as Json[]).map(c => {
      const t = byCluster.get(c.id);
      const majority = t ? [...t.forms].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null : null;
      return { id: c.id, label: c.label, count: c.count, coherence: c.coherence, form: c.form, issue: c.issue,
        exceptions: t?.own ?? 0, assigned: t?.assigned ?? 0, rejected: t?.rejected ?? 0, majority, representatives: JSON.parse(c.representatives) };
    }) };
}

const member = (r: Json) => ({ id: r.id, image: r.image, form: r.form, basis: basis(r as any),
  reported: r.issue, character: r.issue_character });

async function members(env: Env, clusterId: string, q: URLSearchParams, tools: FormTools) {
  const cluster = await env.DB.prepare('SELECT id,form,issue,count FROM form_clusters WHERE id=?').bind(clusterId).first<Json>();
  if (!cluster) tools.fail(404, 'Unknown cluster.');
  const offset = count(q, 'offset', 0, 1_000_000, tools), limit = count(q, 'limit', 120, 500, tools);
  const order = q.get('order') === 'unusual' ? 'unusual' : 'typical';
  const rows = await env.DB.prepare(`SELECT id,image,rank,similarity,form,glyph_set,issue,issue_character FROM form_units WHERE cluster=? ORDER BY rank ${order === 'unusual' ? 'DESC' : 'ASC'} LIMIT ? OFFSET ?`)
    .bind(clusterId, limit, offset).all<Json>();
  return { id: clusterId, total: cluster!.count, offset, order, form: cluster!.form, issue: cluster!.issue,
    items: rows.results.map(r => ({ ...member(r), rank: r.rank, similarity: r.similarity })) };
}

async function split(env: Env, clusterId: string, q: URLSearchParams, tools: FormTools) {
  const k = count(q, 'k', 4, 8, tools), shown = count(q, 'shown', 60, 240, tools);
  if (k < 2) tools.fail(422, 'Invalid k.');
  const rows = await env.DB.prepare('SELECT id,image,split,form,glyph_set,issue,issue_character FROM form_units WHERE cluster=? ORDER BY rank').bind(clusterId).all<Json>();
  if (!rows.results.length) tools.fail(404, 'Unknown cluster.');
  // The groups for every k were computed at publication, one digit per k from 2 to 8.
  const groups = new Map<string, Json[]>();
  for (const r of rows.results) {
    const g = r.split[k - 2];
    groups.set(g, [...(groups.get(g) ?? []), r]);
  }
  return { id: clusterId, k, groups: [...groups.values()].sort((a, b) => b.length - a.length).map(g => ({
    count: g.length, ids: g.map(r => r.id),
    items: g.slice(0, shown).map(member) })) };
}

async function decide(env: Env, request: Request, tools: FormTools) {
  const input = await tools.body(request);
  const actor = tools.text(input.client_id, 128, 'reviewer', true)!;
  const note = tools.text(input.note ?? '', 2000, 'note') ?? '';
  const kind = input.kind;
  if (!['cluster', 'glyph', 'inherit'].includes(kind)) tools.fail(422, 'Unknown decision kind.');
  const form = input.form == null ? null : tools.text(input.form, 8, 'form', true);
  if (kind === 'inherit' && form != null) tools.fail(422, 'Following the cluster takes no form.');
  // A report: the glyphs are not this family's character, or their crop is bad. They keep no form.
  // A cluster can also be marked as mixed.
  const issue = input.issue ?? null;
  if (issue != null && (kind === 'inherit' || form != null || !(kind === 'cluster' ? CLUSTER_ISSUES : ['character', 'crop']).includes(issue)))
    tools.fail(422, 'Only glyphs or clusters without a form can be reported, as a wrong character or a bad crop; only a cluster can be mixed.');
  const character = input.character == null ? null : tools.text(input.character, 8, 'character') || null;
  if (character != null && issue !== 'character') tools.fail(422, 'Only a wrong character names what the glyph is.');
  // A glyph reported as another character joins that character's family.
  const characterFamily = character ? await tools.family(env, character) : null;
  let family: string, clusterId: string | null = null, units: string[] = [];
  if (kind === 'cluster') {
    const cluster = await env.DB.prepare('SELECT id,family FROM form_clusters WHERE id=?').bind(tools.text(input.cluster, 200, 'cluster', true)).first<Json>();
    if (!cluster) tools.fail(422, 'Unknown cluster.');
    family = cluster!.family; clusterId = cluster!.id;
  } else {
    if (!Array.isArray(input.units) || !input.units.length || input.units.length > GLYPHS_PER_DECISION
        || input.units.some((u: unknown) => typeof u !== 'string' || u.length > 200) || new Set(input.units).size !== input.units.length)
      tools.fail(422, `Choose between 1 and ${GLYPHS_PER_DECISION} distinct glyphs.`);
    units = input.units;
    const found = await env.DB.prepare('SELECT count(*) AS n,count(DISTINCT family) AS families,min(family) AS family FROM form_units WHERE id IN (SELECT value FROM json_each(?))')
      .bind(JSON.stringify(units)).first<Json>();
    if (found!.n !== units.length) tools.fail(422, 'Some glyphs are not in the current clustering.');
    if (found!.families !== 1) tools.fail(422, 'A decision covers glyphs of one family.');
    family = found!.family;
  }
  const allowed = await env.DB.prepare('SELECT forms,revision FROM form_families WHERE code_point=?').bind(family!).first<Json>();
  if (form != null && !JSON.parse(allowed!.forms).some((f: Json) => f.char === form)) tools.fail(422, `${form} is not a form of this family.`);
  const id = crypto.randomUUID(), at = new Date().toISOString().replace(/\.\d+Z$/, '+00:00');
  const touched = kind === 'cluster' ? 'SELECT id FROM form_units WHERE cluster=?1' : 'SELECT value FROM json_each(?1)';
  const target = kind === 'cluster' ? clusterId : JSON.stringify(units);
  // Quick review deals unnamed corpus glyphs by character and counts them per character: the glyphs
  // leave their old count, take their form (or, with none decided, the character they had before any
  // decision covered them), and join the new count.
  const unnamed = `FROM corpus_units WHERE named=0 AND character IS NOT NULL AND id IN (${touched})`;
  const statements = [
    kind === 'cluster'
      ? env.DB.prepare(`INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note,issue,character,character_family) SELECT ?,?,?,'cluster',?,?,?,?,json_group_array(id),?,?,?,? FROM (SELECT id FROM form_units WHERE cluster=? ORDER BY rank)`)
        .bind(id, at, actor, family!, form, clusterId, allowed!.revision, note, issue, character, characterFamily, clusterId)
      : env.DB.prepare('INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note,issue,character,character_family) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)')
        .bind(id, at, actor, kind, family!, form, null, allowed!.revision, target, note, issue, character, characterFamily),
    // A mixed cluster names nothing for its glyphs: they lose any form or report it gave them before.
    kind === 'cluster'
      ? env.DB.prepare(`UPDATE form_units SET cluster_form=?1,cluster_issue=?2,cluster_character=?3,cluster_family=?4 WHERE cluster=?5`)
        .bind(form, issue === 'mixed' ? null : issue, character, characterFamily, clusterId)
      : kind === 'glyph'
        ? env.DB.prepare('UPDATE form_units SET glyph_set=1,glyph_form=?1,glyph_decision=?2,glyph_issue=?4,glyph_character=?5,glyph_family=?6 WHERE id IN (SELECT value FROM json_each(?3))')
          .bind(form, id, target, issue, character, characterFamily)
        : env.DB.prepare('UPDATE form_units SET glyph_set=0,glyph_form=NULL,glyph_decision=NULL,glyph_issue=NULL,glyph_character=NULL,glyph_family=NULL WHERE id IN (SELECT value FROM json_each(?))').bind(target),
    env.DB.prepare(`UPDATE form_units SET ${SETTLE} WHERE id IN (${touched})`).bind(target),
    env.DB.prepare(`INSERT OR IGNORE INTO form_bases(id,character,family) SELECT id,character,family FROM corpus_units WHERE id IN (${touched})`).bind(target),
    env.DB.prepare(`UPDATE corpus_characters SET n=n-t.k FROM (SELECT character,production,count(*) AS k ${unnamed} GROUP BY character,production) AS t
      WHERE corpus_characters.character=t.character AND corpus_characters.production=t.production`).bind(target),
    env.DB.prepare(`UPDATE corpus_units SET character=CASE WHEN f.glyph_set=1 OR f.form IS NOT NULL OR f.issue IS NOT NULL THEN coalesce(f.issue_character,f.form) ELSE b.character END,
      family=coalesce(f.issue_family,b.family)
      FROM form_units f JOIN form_bases b ON b.id=f.id WHERE f.id=corpus_units.id AND corpus_units.named=0 AND corpus_units.id IN (${touched})`).bind(target),
    env.DB.prepare(`INSERT INTO corpus_characters(character,production,n,named) SELECT character,production,count(*),0 ${unnamed}
      GROUP BY character,production ON CONFLICT(character,production) DO UPDATE SET n=n+excluded.n`).bind(target),
    env.DB.prepare('DELETE FROM corpus_characters WHERE n=0'),
    env.DB.prepare(`UPDATE form_families SET assigned=(SELECT count(*) FROM form_units WHERE family=?1 AND form IS NOT NULL),
      rejected=(SELECT count(*) FROM form_units WHERE family=?1 AND issue IS NOT NULL) WHERE code_point=?1`).bind(family!),
  ];
  if (kind === 'cluster') statements.push(env.DB.prepare('UPDATE form_clusters SET form=?,issue=?,decision=? WHERE id=?').bind(form, issue, id, clusterId));
  await env.DB.batch(statements);
  const covered = kind === 'cluster'
    ? (await env.DB.prepare('SELECT count FROM form_clusters WHERE id=?').bind(clusterId).first<Json>())!.count : units.length;
  return { id, at, kind, family: family!, form, cluster: clusterId, count: covered, issue, character };
}

async function decisionLog(env: Env) {
  const rows = await env.DB.prepare('SELECT * FROM form_decisions ORDER BY at,seq').all<Json>();
  const lines = rows.results.map(r => JSON.stringify({ id: r.id, at: r.at, actor: r.actor, kind: r.kind, family: r.family,
    form: r.form, cluster: r.cluster, revision: r.revision, units: JSON.parse(r.units), note: r.note,
    ...(r.issue ? { issue: r.issue } : {}), ...(r.character ? { character: r.character } : {}) }));
  return new Response(lines.join('\n') + (lines.length ? '\n' : ''), { headers: {
    'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store',
    'content-disposition': 'attachment; filename="form-decisions.jsonl"' } });
}

// A CODH record as published, shown with the form a person has since named for it.
export async function withForm(env: Env, record: Json, tools: Pick<FormTools, 'codePoints'>): Promise<Json> {
  const row = await env.DB.prepare(`SELECT ${FORM_COLUMNS} FROM form_units WHERE id=?`).bind(record.id).first<UnitForm>();
  return formed(record, row, tools);
}
// The `form_units` columns `formed` reads, for a query that joins them to its own rows.
export const FORM_COLUMNS = 'id,cluster,form,glyph_set,cluster_form,issue,issue_character,issue_family';
export type { UnitForm };
export function formed(record: Json, row: UnitForm | null, tools: Pick<FormTools, 'codePoints'>): Json {
  if (!row || (!row.form && !row.issue && !row.glyph_set)) return row ? { ...record, form_cluster: { id: row.cluster } } : record;
  const decided = { form_cluster: { id: row.cluster }, form_decision: { form: row.form, basis: basis(row) } };
  // A glyph reported as another character shows that character; one only marked off its form shows none.
  const written = row.form ?? row.issue_character;
  if (!written) return { ...record, ...decided, written_character: null, identity_status: 'unassigned', identity_basis: basis(row) ?? 'form_glyph' };
  return { ...record, ...decided, written_character: written, label: written, char: written, code_point: tools.codePoints(written),
    identity_status: 'assigned', identity_basis: basis(row) ?? 'form_glyph', ...(row.issue_family ? { grapheme: row.issue_family } : {}) };
}

function decoded(segment: string, tools: FormTools) {
  try { return decodeURIComponent(segment) } catch { return tools.fail(404, 'Unknown family or cluster.') }
}

// The family list and a family's cluster tallies change only when a decision is made or a clustering
// is loaded, and every page reads the list (the header links to Forms while it answers). The edge
// keeps one copy per finished load (`forms_loaded_at`, written last by a reload), clustering revision
// and latest decision; FORMS_TTL bounds a copy's life.
const FORMS_TTL = 3600;
type FormsState = { loading: number; loaded: string | null; revision: string | null; decision: number | null };
async function cached(url: URL, state: FormsState, key: string, read: () => Promise<Json | null>, ctx: ExecutionContext) {
  const request = new Request(`${url.origin}/atlas/forms/cached/${key}?v=${encodeURIComponent(`${state.loaded}:${state.revision}:${state.decision ?? 0}`)}`);
  const hit = await caches.default.match(request);
  if (hit) return hit.json<Json>();
  const value = await read();
  if (value) ctx.waitUntil(caches.default.put(request, Response.json(value, { headers: { 'cache-control': `public, max-age=${FORMS_TTL}` } })));
  return value;
}

export async function formsRoute(env: Env, request: Request, path: string, q: URLSearchParams, tools: FormTools, ctx: ExecutionContext): Promise<Response | Json | null> {
  const state = (await env.DB.prepare(`SELECT EXISTS(SELECT 1 FROM form_loading) AS loading,(SELECT value FROM metadata WHERE key='forms_loaded_at') AS loaded,
    (SELECT revision FROM form_families LIMIT 1) AS revision,(SELECT max(rowid) FROM form_decisions) AS decision`).first<FormsState>())!;
  // A publication is reloading the clustering; the migration's trigger refuses a decision meanwhile.
  if (path !== '/atlas/forms/decisions.jsonl' && state.loading)
    tools.fail(503, 'The forms are being republished. Try again in a few minutes.');
  if (request.method === 'POST') return path === '/atlas/forms/decisions' ? decide(env, request, tools) : null;
  const url = new URL(request.url);
  if (path === '/atlas/forms/families')
    return (await cached(url, state, 'families', () => families(env), ctx)) ?? tools.fail(404, 'No clustering has been published.');
  if (path === '/atlas/forms/decisions.jsonl') return decisionLog(env);
  const familyPath = path.match(/^\/atlas\/forms\/families\/([^/]+)$/);
  if (familyPath) {
    const code = decoded(familyPath[1], tools), order = q.get('order') === 'size' ? 'size' : 'shape';
    return cached(url, state, `family/${encodeURIComponent(code)}/${order}`, () => family(env, code, q, tools), ctx);
  }
  const clusterPath = path.match(/^\/atlas\/forms\/clusters\/(.+)$/);
  if (clusterPath) return members(env, decoded(clusterPath[1], tools), q, tools);
  const splitPath = path.match(/^\/atlas\/forms\/split\/(.+)$/);
  if (splitPath) return split(env, decoded(splitPath[1], tools), q, tools);
  return null;
}
