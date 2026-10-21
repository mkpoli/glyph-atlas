// The forms tool on glyph-atlas-v3 (decision record 0008): families and clusters with their decisions in
// ATLAS (`form_families`, `form_clusters`, `form_glyphs`, `form_state`), each cluster's glyphs by rank in
// a content-named R2 list `v3/forms/<list>.json.gz`, and each family's first and last glyphs per cluster
// in its index object `v3/forms/families/<list>.json.gz`. A glyph's form is its `form_glyphs` row's, else
// its cluster's. A decision writes its rows and the family's recount in one batch; the unnamed corpus
// glyphs it covers move `crops.char` to their form shortly after (`followCorpus`).
import { publicId } from '../atlas3';
import { StaleCrop, staleResponse, writtenCropField } from '../atlas3Crops';
import { axisOf } from '../dating';
import { type FormTools, GLYPHS_PER_DECISION, isDescription } from '../forms';
import { writesPaused } from '../freeze';

type Json = Record<string, any>;
// A list entry: crop, rank, similarity, tile media key, split groups (one digit per k from 2 to 8),
// tone, image size. An index entry: crop, rank, tile media key, tone, image size.
type ListEntry = [number, number, number, string | null, string, string | null, [number, number] | null];
type IndexEntry = [number, number, string | null, string | null, [number, number] | null];
type FamilyIndex = { family: string; clusters: Record<string, { list: string; count: number; head: IndexEntry[]; tail: IndexEntry[] }> };
type ClusterRow = { id: number; key: string; family: string; label: string; count: number; coherence: number; list: string;
  form: string | null; issue: string | null; character: string | null; written_family: string | null };
// A glyph's own decision, from `form_glyphs`.
type Own = { crop: number; cluster: number | null; form: string | null; issue: string | null; character: string | null };

// The index object holds per cluster its first HEAD glyphs (the representatives) and its last ones.
const HEAD = 12;
// What a cluster decision can say instead of a form: its glyphs differ, or none is this family's
// character, or none is cropped as one glyph. The last two are reported on each glyph.
const CLUSTER_ISSUES = ['mixed', 'character', 'crop'];
const GEN = '(SELECT gen FROM generation WHERE id=0)';

function count(q: URLSearchParams, key: string, fallback: number, max: number, tools: FormTools) {
  const n = Number(q.get(key) ?? fallback);
  if (!Number.isSafeInteger(n) || n < 0 || n > max) tools.fail(422, `Invalid ${key}.`);
  return n;
}
const image = (key: string | null) => key ? `/atlas/media/${key}.webp` : null;
// A cluster's issue as its glyphs take it: 'mixed' gives them none.
const glyphIssue = (c: Pick<ClusterRow, 'issue'>) => c.issue === 'mixed' ? null : c.issue;
// What a glyph ends with: its own decision, else its cluster's.
function settled(c: ClusterRow, own: Own | undefined) {
  if (own) return { form: own.form, issue: own.issue, character: own.character, basis: 'form_glyph' as const };
  const issue = glyphIssue(c);
  return { form: c.form, issue, character: c.character, basis: c.form || issue ? 'form_cluster' as const : null };
}
// What is painted in a member's place until it loads.
const member = (crop: number, key: string | null, s: ReturnType<typeof settled>, tone: string | null, size: [number, number] | null) =>
  ({ id: publicId(crop), image: image(key), form: s.form, basis: s.basis, reported: s.issue, character: s.character, tone, image_size: size });

// Content-named objects never change, so an isolate keeps the last few it read.
const OBJECTS_HELD = 64;
const objects = new Map<string, Promise<Json | null>>();
function object<T>(env: Env, key: string): Promise<T | null> {
  const hit = objects.get(key);
  if (hit) { objects.delete(key); objects.set(key, hit); return hit as Promise<T | null>; }
  const read = env.MEDIA.get(key).then(async found => found
    ? await new Response(found.body.pipeThrough(new DecompressionStream('gzip'))).json() as Json : null);
  read.then(found => { if (!found && objects.get(key) === read) objects.delete(key) }, () => { if (objects.get(key) === read) objects.delete(key) });
  objects.set(key, read);
  while (objects.size > OBJECTS_HELD) objects.delete(objects.keys().next().value!);
  return read as Promise<T | null>;
}
async function listOf(env: Env, c: Pick<ClusterRow, 'list'>, tools: FormTools): Promise<ListEntry[]> {
  return (await object<{ members: ListEntry[] }>(env, `v3/forms/${c.list}.json.gz`) ?? tools.fail(503, 'The forms publication is incomplete.', { code: 'busy' })).members;
}

async function families(env: Env) {
  const rows = await env.ATLAS.prepare('SELECT code_point,char,label,count,cluster_count,assigned,rejected,revision FROM form_families ORDER BY count DESC,code_point')
    .all<Json>();
  if (!rows.results.length) return null;
  return { revision: rows.results[0].revision, items: rows.results.map(r => ({ code_point: r.code_point, char: r.char,
    label: r.label, count: r.count, clusters: r.cluster_count, assigned: r.assigned, rejected: r.rejected })) };
}

// The code points that have a family, which the site reads to link a character to its forms.
async function codes(env: Env) {
  const rows = await env.ATLAS.prepare('SELECT code_point FROM form_families ORDER BY code_point').all<{ code_point: string }>();
  return rows.results.length ? { items: rows.results.map(r => r.code_point) } : null;
}

// The family's clusters in the order asked, along `form_cluster_shape` or `form_cluster_size`.
export const familyClustersQuery = (order: 'shape' | 'size') =>
  `SELECT id,key,family,label,count,coherence,list,form,issue,character,written_family FROM form_clusters WHERE family=? ORDER BY ${order === 'size' ? 'size_position' : 'shape_position'}`;
// The family's glyphs with their own decision: its clusters by `form_cluster_shape`, then each one's
// rows by `form_glyph_cluster`.
export const familyGlyphsQuery = () => `SELECT g.crop,g.cluster,g.form,g.issue,g.character FROM form_clusters c
  CROSS JOIN form_glyphs g ON g.cluster=c.id WHERE c.family=?`;

// Description claims on the crop's effective cut, read from indexed description ranges.
export const describedClaimsQuery = () => `SELECT r.value AS form,count(*) AS n FROM
  (SELECT id,value FROM representations WHERE value>='⿰' AND value<'　' AND scheme='ids'
   UNION ALL SELECT id,value FROM representations WHERE value>='〾' AND value<'〿' AND scheme='ids'
   UNION ALL SELECT id,value FROM representations WHERE value>='㇯' AND value<'ㇰ' AND scheme='ids') r
  CROSS JOIN forms f ON f.anchor=r.id CROSS JOIN current_claims c ON c.predicate='has_form' AND c.object=f.id
  CROSS JOIN crops k ON k.id=c.crop CROSS JOIN form_clusters g ON g.id=k.cluster
  LEFT JOIN reviews v ON v.crop=k.id
  WHERE g.family=? AND c.status<>'rejected' AND c.cut=k.cut+(v.x IS NOT NULL) GROUP BY r.value`;

const mostUsed = (counts: Map<string, number>) =>
  [...counts].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([char, count]) => ({ char, count }));

async function family(env: Env, codePoint: string, q: URLSearchParams, tools: FormTools) {
  const found = await env.ATLAS.prepare('SELECT * FROM form_families WHERE code_point=?').bind(codePoint).first<Json>();
  if (!found) tools.fail(404, 'This family was not clustered.');
  const order = q.get('order') === 'size' ? 'size' : 'shape';
  const [clusterRows, ownRows, claimRows] = await env.ATLAS.batch([
    env.ATLAS.prepare(familyClustersQuery(order)).bind(codePoint),
    env.ATLAS.prepare(familyGlyphsQuery()).bind(codePoint),
    env.ATLAS.prepare(describedClaimsQuery()).bind(codePoint),
  ]);
  const clusters = clusterRows.results as ClusterRow[];
  const index = await object<FamilyIndex>(env, `v3/forms/families/${found!.list}.json.gz`) ?? tools.fail(503, 'The forms publication is incomplete.', { code: 'busy' });
  const own = new Map<number, Map<number, Own>>();
  for (const g of ownRows.results as Own[]) own.set(g.cluster!, (own.get(g.cluster!) ?? new Map()).set(g.crop, g));
  // Each cluster's glyphs by form: those without their own decision take the cluster's.
  const tallies = new Map(clusters.map(c => {
    const mine = [...own.get(c.id)?.values() ?? []], rest = c.count - mine.length, forms = new Map<string, number>();
    if (c.form && rest > 0) forms.set(c.form, rest);
    for (const g of mine) if (g.form) forms.set(g.form, (forms.get(g.form) ?? 0) + 1);
    const assigned = [...forms.values()].reduce((a, b) => a + b, 0);
    const rejected = (glyphIssue(c) ? rest : 0) + mine.filter(g => g.issue).length;
    // A tie goes to the lower form, as on the local review server.
    const [majority, majorityCount] = [...forms].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0] ?? [null, 0];
    return [c.id, { own: mine.length, assigned, rejected, forms, majority, majorityCount, form: c.form ?? majority }];
  }));
  // The descriptions named by forms decisions or current crop claims, most used first: the palette
  // offers them after the encoded forms. How many of the family's glyphs each form names, most used first.
  const described = new Map<string, number>(), usage = new Map<string, number>();
  for (const t of tallies.values()) for (const [form, n] of t.forms) {
    usage.set(form, (usage.get(form) ?? 0) + n);
    if (isDescription(form)) described.set(form, (described.get(form) ?? 0) + n);
  }
  for (const row of claimRows.results as Json[]) if (isDescription(row.form))
    described.set(row.form, (described.get(row.form) ?? 0) + row.n);
  // A cluster's glyphs named otherwise, by form, twelve a form, most typical first; a cluster reported
  // as a whole has none. Only a glyph with its own decision can differ, and its rank is in the list.
  // Each cluster's twelve glyphs least like its centre, past the twelve most typical, leaving out
  // reported ones: from the index's last glyphs, or the list when too many of those are reported.
  const items = await Promise.all(clusters.map(async c => {
    const t = tallies.get(c.id)!, mine = own.get(c.id) ?? new Map<number, Own>(), entry = index.clusters[String(c.id)];
    if (!entry) tools.fail(503, 'The forms publication is incomplete.', { code: 'busy' });
    const strayGlyphs = !c.issue && t.form ? [...mine.values()].filter(g => g.form && g.form !== t.form && !g.issue) : [];
    const issueOf = (crop: number) => mine.has(crop) ? mine.get(crop)!.issue : glyphIssue(c);
    const typical = (entries: [number, number, string | null][]) => entries.filter(([crop, rank]) => rank >= HEAD && !issueOf(crop))
      .sort((a, b) => b[1] - a[1]).slice(0, 12);
    let unusual = typical(entry.tail.map(e => [e[0], e[1], e[2]]));
    const list = strayGlyphs.length || (unusual.length < 12 && c.count > HEAD + entry.tail.length) ? await listOf(env, c, tools) : null;
    if (list && unusual.length < 12) unusual = typical(list.map(e => [e[0], e[1], e[3]]));
    const strays = new Map<string, { id: string; image: string | null }[]>();
    if (strayGlyphs.length) {
      const formOf = new Map(strayGlyphs.map(g => [g.crop, g.form!]));
      for (const e of list!) {
        const form = formOf.get(e[0]);
        if (!form) continue;
        const shown = strays.get(form) ?? [];
        if (shown.length < 12) strays.set(form, [...shown, { id: publicId(e[0]), image: image(e[3]) }]);
      }
    }
    return { id: c.key, label: c.label, count: c.count, coherence: c.coherence, form: c.form, issue: c.issue,
      exceptions: t.own, assigned: t.assigned, rejected: t.rejected, majority: t.majority, majority_count: t.majorityCount,
      representatives: entry.head.slice(0, HEAD).map(e => ({ id: publicId(e[0]), image: image(e[2]) })),
      unusual: unusual.map(([crop, , key]) => ({ id: publicId(crop), image: image(key) })),
      strays: [...strays].map(([form, glyphs]) => ({ form, count: t.forms.get(form) ?? 0, glyphs }))
        .sort((a, b) => b.count - a.count || (a.form < b.form ? -1 : 1)) };
  }));
  return { revision: found!.revision, code_point: found!.code_point, char: found!.char, label: found!.label,
    count: found!.count, clusters: found!.cluster_count, assigned: found!.assigned, rejected: found!.rejected, order,
    forms: JSON.parse(found!.forms), described: mostUsed(described), usage: mostUsed(usage), items };
}

export const clusterQuery = () => 'SELECT id,key,family,label,count,coherence,list,form,issue,character,written_family FROM form_clusters WHERE key=?';
// A page of a cluster's glyphs: each one's own decision and book, by primary key.
export const memberRowsQuery = () => `SELECT j.value AS crop,c.book,b.key AS document,g.crop IS NOT NULL AS own,g.form,g.issue,g.character
  FROM json_each(?) j LEFT JOIN crops c ON c.id=j.value LEFT JOIN books b ON b.id=c.book LEFT JOIN form_glyphs g ON g.crop=j.value`;
// The dates of the books those glyphs come from, by `book_dates`' key.
export const memberDatesQuery = () => `SELECT b.key AS document,d.* FROM json_each(?) j JOIN books b ON b.id=j.value
  JOIN book_dates d ON d.book=b.id`;

async function members(env: Env, key: string, q: URLSearchParams, tools: FormTools) {
  const cluster = await env.ATLAS.prepare(clusterQuery()).bind(key).first<ClusterRow>();
  if (!cluster) tools.fail(404, 'Unknown cluster.');
  const offset = count(q, 'offset', 0, 1_000_000, tools), limit = count(q, 'limit', 120, 500, tools);
  const order = q.get('order') === 'unusual' ? 'unusual' : 'typical';
  const list = await listOf(env, cluster!, tools);
  const page = (order === 'unusual' ? [...list].reverse() : list).slice(offset, offset + limit);
  const rows = page.length ? (await env.ATLAS.prepare(memberRowsQuery()).bind(JSON.stringify(page.map(e => e[0]))).all<Json>()).results : [];
  const byCrop = new Map(rows.map(r => [r.crop as number, r]));
  const books = [...new Set(rows.map(r => r.book).filter(book => book != null))];
  const dated = books.length ? (await env.ATLAS.prepare(memberDatesQuery()).bind(JSON.stringify(books)).all<Json>()).results : [];
  const dating = new Map<string, Json>();
  for (const row of dated) dating.set(row.document, { ...dating.get(row.document), [row.axis]: axisOf(row) });
  // Each glyph carries the dates of its book.
  return { id: cluster!.key, total: cluster!.count, offset, order, form: cluster!.form, issue: cluster!.issue,
    items: page.map(([crop, rank, similarity, tile, , tone, size]) => {
      const r = byCrop.get(crop), document = r?.document ?? null;
      return { ...member(crop, tile, settled(cluster!, r?.own ? r as Own : undefined), tone, size), rank, similarity, document,
        dating: dating.get(document ?? '') ?? {} };
    }) };
}

// A cluster's glyphs with their own decision, along `form_glyph_cluster`.
export const clusterGlyphsQuery = () => 'SELECT crop,cluster,form,issue,character FROM form_glyphs WHERE cluster=?';

async function split(env: Env, key: string, q: URLSearchParams, tools: FormTools) {
  const k = count(q, 'k', 4, 8, tools), shown = count(q, 'shown', 60, 240, tools);
  if (k < 2) tools.fail(422, 'Invalid k.');
  const cluster = await env.ATLAS.prepare(clusterQuery()).bind(key).first<ClusterRow>();
  if (!cluster) tools.fail(404, 'Unknown cluster.');
  const [list, ownRows] = await Promise.all([listOf(env, cluster!, tools),
    env.ATLAS.prepare(clusterGlyphsQuery()).bind(cluster!.id).all<Own>()]);
  if (!list.length) tools.fail(404, 'Unknown cluster.');
  const own = new Map(ownRows.results.map(g => [g.crop, g]));
  // The groups for every k were computed at publication, one digit per k from 2 to 8. A group's
  // previews carry no tone or size.
  const groups = new Map<string, ListEntry[]>();
  for (const e of list) groups.set(e[4][k - 2], [...(groups.get(e[4][k - 2]) ?? []), e]);
  return { id: cluster!.key, k, groups: [...groups.values()].sort((a, b) => b.length - a.length).map(g => ({
    count: g.length, ids: g.map(e => publicId(e[0])),
    items: g.slice(0, shown).map(e => member(e[0], e[3], settled(cluster!, own.get(e[0])), null, null)) })) };
}

// A character's grapheme family: its head's code points, else its own.
async function familyOf(env: Env, char: string, tools: FormTools) {
  const row = await env.ATLAS.prepare('SELECT h.text FROM graphemes g JOIN graphemes h ON h.id=g.head WHERE g.text=?').bind(char).first<{ text: string }>();
  return tools.codePoints(row?.text ?? char);
}

// The family's glyphs with a form and those reported, from its clusters and their glyphs' own
// decisions, along `form_cluster_shape` and `form_glyph_cluster`.
export const recountQuery = () => `UPDATE form_families SET
  assigned=(SELECT coalesce(sum(CASE WHEN c.form IS NOT NULL THEN c.count-(SELECT count(*) FROM form_glyphs g WHERE g.cluster=c.id) ELSE 0 END),0)
    +(SELECT count(*) FROM form_clusters c CROSS JOIN form_glyphs g ON g.cluster=c.id WHERE c.family=?1 AND g.form IS NOT NULL)
    FROM form_clusters c WHERE c.family=?1),
  rejected=(SELECT coalesce(sum(CASE WHEN c.issue IS NOT NULL AND c.issue<>'mixed' THEN c.count-(SELECT count(*) FROM form_glyphs g WHERE g.cluster=c.id) ELSE 0 END),0)
    +(SELECT count(*) FROM form_clusters c CROSS JOIN form_glyphs g ON g.cluster=c.id WHERE c.family=?1 AND g.issue IS NOT NULL)
    FROM form_clusters c WHERE c.family=?1)
  WHERE code_point=?1`;
// How many of a decision's crops are clustered, and in how many families, by primary key.
export const glyphFamiliesQuery = () => `SELECT count(*) AS n,count(DISTINCT k.family) AS families,min(k.family) AS family
  FROM json_each(?) j JOIN crops c ON c.id=j.value JOIN form_clusters k ON k.id=c.cluster`;
// The unnamed corpus glyphs among a decision's crops, listed for `followCorpus`.
export const listFollowQuery = () => `INSERT OR IGNORE INTO corpus_follow(crop) SELECT c.id FROM json_each(?) j
  JOIN crops c ON c.id=j.value WHERE c.kind=1 AND c.revision=0`;

async function decide(env: Env, request: Request, tools: FormTools, actor: string, ctx: ExecutionContext): Promise<Json | Response> {
  const input = await tools.body(request);
  const note = tools.text(input.note ?? '', 2000, 'note') ?? '';
  const kind = input.kind;
  if (!['cluster', 'glyph', 'inherit'].includes(kind)) tools.fail(422, 'Unknown decision kind.');
  // A form is one of the family's characters, or a shape Unicode lacks written as a description.
  const form = input.form == null ? null : tools.text(input.form, 256, 'form', true);
  if (kind === 'inherit' && form != null) tools.fail(422, 'Following the cluster takes no form.');
  // A report: the glyphs are not this family's character, or their crop is bad. They keep no form.
  // A cluster can also be marked as mixed.
  const issue = input.issue ?? null;
  if (issue != null && (kind === 'inherit' || form != null || !(kind === 'cluster' ? CLUSTER_ISSUES : ['character', 'crop']).includes(issue)))
    tools.fail(422, 'Only glyphs or clusters without a form can be reported, as a wrong character or a bad crop; only a cluster can be mixed.');
  const character = input.character == null ? null : tools.text(input.character, 8, 'character') || null;
  if (character != null && issue !== 'character') tools.fail(422, 'Only a wrong character names what the glyph is.');
  // A glyph takes the grapheme of what it is written as: the form named for it, or the character it
  // is reported as. A kana form keeps its family (𛂞 is は's); a kanji variant has its own (仿 is not 倣's).
  // A description is a form of the family it is named in.
  const written = form ?? character, described = form != null && isDescription(form);
  let family: string, cluster: ClusterRow | null = null, crops: number[] = [];
  if (kind === 'cluster') {
    cluster = await env.ATLAS.prepare(clusterQuery()).bind(tools.text(input.cluster, 200, 'cluster', true)).first<ClusterRow>();
    if (!cluster) tools.fail(422, 'Unknown cluster.');
    family = cluster!.family;
    crops = (await listOf(env, cluster!, tools)).map(e => e[0]);
  } else {
    if (!Array.isArray(input.units) || !input.units.length || input.units.length > GLYPHS_PER_DECISION
        || input.units.some((u: unknown) => typeof u !== 'string' || u.length > 200) || new Set(input.units).size !== input.units.length)
      tools.fail(422, `Choose between 1 and ${GLYPHS_PER_DECISION} distinct glyphs.`);
    try {
      crops = await Promise.all((input.units as string[]).map(async u => await writtenCropField(env, u, ctx) ?? -1));
    } catch (error) {
      if (error instanceof StaleCrop) return staleResponse();
      throw error;
    }
    if (new Set(crops).size !== crops.length) tools.fail(422, `Choose between 1 and ${GLYPHS_PER_DECISION} distinct glyphs.`);
    const found = await env.ATLAS.prepare(glyphFamiliesQuery()).bind(JSON.stringify(crops)).first<Json>();
    if (found!.n !== crops.length) tools.fail(422, 'Some glyphs are not in the current clustering.');
    if (found!.families !== 1) tools.fail(422, 'A decision covers glyphs of one family.');
    family = found!.family;
  }
  const allowed = await env.ATLAS.prepare('SELECT forms,revision FROM form_families WHERE code_point=?').bind(family!).first<Json>();
  if (form != null && !described && !JSON.parse(allowed!.forms).some((f: Json) => f.char === form)) tools.fail(422, `${form} is not a form of this family.`);
  const writtenFamily = described ? family! : written ? await familyOf(env, written, tools) : null;
  const id = crypto.randomUUID(), at = new Date().toISOString().replace(/\.\d+Z$/, '+00:00');
  const target = JSON.stringify(crops);
  // What the touched rows held before, read in the same transaction: the decisions that restore it
  // are returned as the decision's undo.
  const before = kind === 'cluster'
    ? env.ATLAS.prepare('SELECT form,issue,character FROM form_clusters WHERE id=?').bind(cluster!.id)
    : env.ATLAS.prepare('SELECT j.value AS crop,g.crop IS NOT NULL AS own,g.form,g.issue,g.character FROM json_each(?) j LEFT JOIN form_glyphs g ON g.crop=j.value ORDER BY j.value').bind(target);
  // A cluster decision names the list it was made on; a glyph decision its crops.
  const units = kind === 'cluster' ? '[]' : JSON.stringify(crops.map(publicId));
  const statements = [
    before,
    env.ATLAS.prepare('INSERT INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,crops,note,issue,character,written_family,list) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
      .bind(id, at, actor, kind, family!, form, cluster?.key ?? null, allowed!.revision, units, kind === 'cluster' ? '[]' : target,
        note, issue, character, writtenFamily, cluster?.list ?? null),
    kind === 'cluster'
      ? env.ATLAS.prepare('UPDATE form_clusters SET form=?,issue=?,character=?,written_family=?,decision=? WHERE id=?')
        .bind(form, issue, character, writtenFamily, id, cluster!.id)
      : kind === 'glyph'
        ? env.ATLAS.prepare(`INSERT INTO form_glyphs(crop,family,cluster,form,issue,character,written_family,decision)
            SELECT c.id,?2,c.cluster,?3,?4,?5,?6,?7 FROM json_each(?1) j JOIN crops c ON c.id=j.value WHERE 1
            ON CONFLICT(crop) DO UPDATE SET family=excluded.family,cluster=excluded.cluster,form=excluded.form,issue=excluded.issue,
              character=excluded.character,written_family=excluded.written_family,decision=excluded.decision`)
          .bind(target, family!, form, issue, character, writtenFamily, id)
        : env.ATLAS.prepare('DELETE FROM form_glyphs WHERE crop IN (SELECT value FROM json_each(?))').bind(target),
    env.ATLAS.prepare(listFollowQuery()).bind(target),
    env.ATLAS.prepare(recountQuery()).bind(family!),
  ];
  let prior: D1Result;
  try {
    [prior] = await env.ATLAS.batch(statements);
  } catch (error) {
    const message = String((error as Error)?.message ?? error);
    // A load began, or replaced the cluster's list, since the reads above (the migration's triggers).
    if (message.includes('The forms are being republished')) tools.fail(503, 'The forms are being republished. Try again in a few minutes.', { code: 'busy' });
    if (message.includes('The cluster has changed')) tools.fail(409, 'The cluster has changed; reload the forms.');
    throw error;
  }
  return { id, at, kind, family: family!, form, cluster: cluster?.key ?? null, count: kind === 'cluster' ? cluster!.count : crops.length,
    issue, character, undo: kind === 'cluster' ? [restoreCluster(cluster!.key, (prior!.results as Json[])[0])] : restoreGlyphs(prior!.results as Json[]) };
}

const stated = (form: string | null, issue: string | null, character: string | null) =>
  ({ form, ...(issue ? { issue } : {}), ...(character ? { character } : {}) });
function restoreCluster(cluster: string, was: Json) {
  return { kind: 'cluster', cluster, ...stated(was.form, was.issue, was.issue === 'character' ? was.character : null) };
}
// Glyphs that followed their cluster follow it again; the others get back their own decision, one
// decision for each distinct one.
function restoreGlyphs(rows: Json[]) {
  const groups = new Map<string, Json>();
  for (const r of rows) {
    const decision = r.own ? { kind: 'glyph', ...stated(r.form, r.issue, r.character) } : { kind: 'inherit' };
    const key = JSON.stringify(decision);
    groups.set(key, { ...decision, units: [...groups.get(key)?.units ?? [], publicId(r.crop)] });
  }
  return [...groups.values()];
}

// Glyphs a corpus batch moves. Each rewrites the crop's index entries, so a batch takes a fraction of a
// second on D1.
export const FOLLOW_BATCH = 500;
const following = `SELECT crop FROM corpus_follow ORDER BY crop LIMIT ${FOLLOW_BATCH}`;
const movable = `FROM crops c WHERE c.id IN (${following}) AND c.kind=1 AND c.revision=0`;
// What each listed unnamed glyph is written as: its own decision's character or form, else its
// cluster's; `decided` is 0 when no decision names a form or report for it.
const writtenAs = `SELECT c.id,CASE WHEN g.crop IS NOT NULL THEN coalesce(g.character,g.form) ELSE coalesce(k.character,k.form) END AS text,
  (g.crop IS NOT NULL OR k.form IS NOT NULL OR (k.issue IS NOT NULL AND k.issue<>'mixed')) AS decided
  FROM crops c LEFT JOIN form_glyphs g ON g.crop=c.id LEFT JOIN form_clusters k ON k.id=c.cluster
  WHERE c.id IN (${following}) AND c.kind=1 AND c.revision=0`;
const cell = 'c.kind,coalesce(c.char,-1),coalesce(c.family,-1),c.book,coalesce(c.production,-1),coalesce(c.style,-1),c.state,c.quiz,coalesce((c.year-CASE WHEN c.year<0 THEN 9 ELSE 0 END)/10*10,-1)';
const tallied = (sign: string) => `INSERT INTO tally(gen,kind,char,family,book,production,style,state,quiz,decade,n)
  SELECT ${GEN},${cell},${sign}count(*) ${movable} GROUP BY ${cell} ON CONFLICT DO UPDATE SET n=n+excluded.n`;
export const followStatements = () => [
  `INSERT OR IGNORE INTO graphemes(text) SELECT DISTINCT text FROM (${writtenAs}) WHERE decided AND text IS NOT NULL`,
  `INSERT OR IGNORE INTO form_bases(crop,char) SELECT c.id,c.char ${movable}`,
  tallied('-'),
  `UPDATE crops SET char=t.char FROM (SELECT w.id,CASE WHEN w.decided THEN (SELECT id FROM graphemes WHERE text=w.text) ELSE b.char END AS char
    FROM (${writtenAs}) w JOIN form_bases b ON b.crop=w.id) AS t WHERE crops.id=t.id AND crops.char IS NOT t.char`,
  tallied(''),
  `DELETE FROM tally WHERE gen=${GEN} AND n=0`,
  `DELETE FROM corpus_follow WHERE crop IN (${following})`,
];
// Unnamed corpus glyphs take their decided form as their character, or, with none decided, the
// character they had before any decision covered them; each leaves its count in `tally` and joins the
// new one in the current generation. A batch reads its glyphs from the list and removes them last, in
// one transaction, so drains running at once never move a glyph twice, and a glyph decided again while
// listed is moved to its latest form.
export async function followCorpus(env: Env) {
  for (;;) {
    const results = await env.ATLAS.batch(followStatements().map(sql => env.ATLAS.prepare(sql)));
    if (results.at(-1)!.meta.changes < FOLLOW_BATCH) return;
  }
}

async function decisionLog(env: Env) {
  const rows = await env.ATLAS.prepare('SELECT * FROM form_decisions ORDER BY at,seq').all<Json>();
  const lines = rows.results.map(r => JSON.stringify({ id: r.id, at: r.at, actor: r.actor, kind: r.kind, family: r.family,
    form: r.form, cluster: r.cluster, revision: r.revision, units: JSON.parse(r.units), note: r.note,
    ...(r.issue ? { issue: r.issue } : {}), ...(r.character ? { character: r.character } : {}), ...(r.list ? { list: r.list } : {}) }));
  return new Response(lines.join('\n') + (lines.length ? '\n' : ''), { headers: {
    'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store',
    'content-disposition': 'attachment; filename="form-decisions.jsonl"' } });
}

function decoded(segment: string, tools: FormTools) {
  try { return decodeURIComponent(segment) } catch { return tools.fail(404, 'Unknown family or cluster.') }
}

// The family list and a family's cluster tallies change only when a decision is made or a clustering
// is loaded, and every page reads the list (the header links to Forms while it answers). The edge
// keeps one copy per load (`form_state.lists`), clustering revision and latest decision; FORMS_TTL
// bounds a copy's life.
const FORMS_TTL = 3600;
// The shape of what `family` and `families` answer; a change to it leaves the older copies behind.
const FORMS_SHAPE = 4;
type FormsState = { loading: number; lists: string; revision: string | null; decision: number | null; following: number; ledger: number | null; event: number | null; publication: number | null };
export const stateQuery = () => `SELECT s.loading,s.lists,(SELECT revision FROM form_families LIMIT 1) AS revision,
  (SELECT max(seq) FROM form_decisions) AS decision,EXISTS(SELECT 1 FROM corpus_follow) AS following,
  (SELECT max(rowid) FROM ledger_submissions) AS ledger,(SELECT max(id) FROM events) AS event,
  (SELECT max(rowid) FROM imports) AS publication FROM form_state s WHERE s.id=0`;
async function cached(url: URL, state: FormsState, key: string, read: () => Promise<Json | null>, ctx: ExecutionContext) {
  const request = new Request(`${url.origin}/atlas/forms/cached/${key}?v=${encodeURIComponent(`${FORMS_SHAPE}:${state.lists}:${state.revision}:${state.decision ?? 0}`)}`);
  const hit = await caches.default.match(request);
  if (hit) return hit.json<Json>();
  const value = await read();
  if (value) ctx.waitUntil(caches.default.put(request, Response.json(value, { headers: { 'cache-control': `public, max-age=${FORMS_TTL}` } })));
  return value;
}

export async function formsRoute(env: Env, request: Request, path: string, q: URLSearchParams, tools: FormTools, ctx: ExecutionContext, actor: string | null = null): Promise<Response | Json | null> {
  const state = (await env.ATLAS.prepare(stateQuery()).first<FormsState>())!;
  // A load is sending the clustering; the migration's trigger refuses a decision meanwhile.
  if (path !== '/atlas/forms/decisions.jsonl' && state.loading)
    tools.fail(503, 'The forms are being republished. Try again in a few minutes.', { code: 'busy' });
  // The decision answers once its own rows are written; its corpus glyphs move after. Glyphs a drain cut
  // short left listed are moved by the next request that finds them.
  const follow = () => ctx.waitUntil(followCorpus(env).catch(error => console.error('corpus follow', error)));
  if (request.method === 'POST') {
    if (path !== '/atlas/forms/decisions') return null;
    if (!actor) tools.fail(401, 'Sign in to decide forms.');
    const decided = await decide(env, request, tools, actor!, ctx);
    if (!(decided instanceof Response)) follow();
    return decided;
  }
  if (state.following && !state.loading && !writesPaused(env)) follow();
  const url = new URL(request.url);
  if (path === '/atlas/forms/families')
    return (await cached(url, state, 'families', () => families(env), ctx)) ?? tools.fail(404, 'No clustering has been published.');
  if (path === '/atlas/forms/codes') {
    const value = (await cached(url, state, 'codes', () => codes(env), ctx)) ?? tools.fail(404, 'No clustering has been published.');
    // Every page asks for these; the browser keeps them a few minutes.
    return Response.json(value, { headers: { 'cache-control': 'public, max-age=300', 'x-content-type-options': 'nosniff' } });
  }
  if (path === '/atlas/forms/decisions.jsonl') return decisionLog(env);
  const familyPath = path.match(/^\/atlas\/forms\/families\/([^/]+)$/);
  if (familyPath) {
    const code = decoded(familyPath[1], tools), order = q.get('order') === 'size' ? 'size' : 'shape';
    return cached(url, state, `family/${encodeURIComponent(code)}/${order}/${state.ledger ?? 0}:${state.event ?? 0}:${state.publication ?? 0}`, () => family(env, code, q, tools), ctx);
  }
  const clusterPath = path.match(/^\/atlas\/forms\/clusters\/(.+)$/);
  if (clusterPath) return members(env, decoded(clusterPath[1], tools), q, tools);
  const splitPath = path.match(/^\/atlas\/forms\/split\/(.+)$/);
  if (splitPath) return split(env, decoded(splitPath[1], tools), q, tools);
  return null;
}
