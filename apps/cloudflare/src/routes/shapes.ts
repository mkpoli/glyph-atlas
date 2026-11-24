// The crops given a 構形 and no form (`built_as`, docs/design/form-model.md): those whose shape waits to
// be named. A crop leaves the list when a form is claimed on it, or its structure taken back.
import { publicId } from '../atlas3';
import { listingItems } from '../atlas3Listing';
import { Problem } from '../busy';

type Json = Record<string, any>;
const FILED = `('asserted','accepted','adjudicated')`;
// Whether a claim's crop waits: the claim holds on the crop's effective cut, and no form is named on it. A
// form people disagree about is one; a shape said to be unresolved is not.
const open = (c: string) => `k.kind<2 AND ${c}.cut=k.cut+(r.x IS NOT NULL) AND NOT EXISTS(SELECT 1 FROM current_claims f WHERE f.crop=${c}.crop AND f.predicate='has_form'
    AND f.scope='' AND f.slot='' AND f.cut=${c}.cut AND f.status<>'rejected' AND (f.object IS NOT NULL OR f.status='disputed'))`;
const CLAIMED = `c.predicate='built_as' AND c.scope='' AND c.slot='' AND c.status IN ${FILED}`;
/** The claims a page reads, by structure and then by crop, each with whether its crop waits (`open`): one
 *  range of `current_claim_object`, read no further than the page, since a claim stays once its crop has a
 *  form. `of` narrows it to one structure, and `after` is the structure and subject the last page ended at. */
export function openShapesQuery(of: string | null, after: [string, string] | null): { sql: string; values: string[] } {
  const values: string[] = [], bind = (value: string) => `?${values.push(value)}`;
  const where = [CLAIMED, of === null ? 'c.object IS NOT NULL' : `c.object=${bind(of)}`,
    ...(after ? [of === null ? `(c.object,c.subject)>(${bind(after[0])},${bind(after[1])})` : `c.subject>${bind(after[1])}`] : [])];
  return { sql: `SELECT claim.object AS structure,s.label,claim.subject,claim.crop,k.id IS NOT NULL AND ${open('claim')} AS open
  FROM (SELECT c.object,c.subject,c.crop,c.cut FROM current_claims c WHERE ${where.join(' AND ')} ORDER BY c.object,c.subject LIMIT ?${values.length + 1}) claim
  LEFT JOIN crops k ON k.id=claim.crop LEFT JOIN reviews r ON r.crop=k.id LEFT JOIN structures s ON s.id=claim.object ORDER BY claim.object,claim.subject`, values };
}
/** How many of these structures' crops wait for a form, by structure; one with none is left out. */
export const openShapeCountsQuery = () => `SELECT c.object AS structure,count(*) AS n FROM current_claims c
  CROSS JOIN crops k ON k.id=c.crop LEFT JOIN reviews r ON r.crop=k.id WHERE c.object IN (SELECT value FROM json_each(?)) AND ${CLAIMED} AND ${open('c')} GROUP BY c.object`;
export async function openShapeCounts(env: Env, structures: string[]): Promise<Map<string, number>> {
  if (!structures.length) return new Map();
  const rows = (await env.ATLAS.prepare(openShapeCountsQuery()).bind(JSON.stringify(structures)).all<{ structure: string; n: number }>()).results;
  return new Map(rows.map(row => [row.structure, row.n]));
}

const STRUCTURE_ID = /^st:[0-9a-f]{32}$/, PAGE = 120;
// A subject can hold any character, which base64 takes as UTF-8 escapes.
const encodeCursor = (after: [string, string]) => btoa(encodeURIComponent(JSON.stringify(after)));
function decodeCursor(value: string): [string, string] {
  try {
    const found = JSON.parse(decodeURIComponent(atob(value)));
    if (!Array.isArray(found) || found.length !== 2 || !found.every(part => typeof part === 'string' && part.length <= 256)) throw 0;
    return found as [string, string];
  } catch { throw new Problem(422, 'Invalid cursor.') }
}
/** `{ items, next, structure }`: listing items, each with its `structure` `{ id, label }`, and where the next
 *  page starts; `structure` is the one the list is narrowed to. A page of claims whose crops have forms by
 *  now has no item and a next. */
export async function openShapes(env: Env, q: URLSearchParams): Promise<Json> {
  const of = q.get('structure') || null, after = q.get('after') ? decodeCursor(q.get('after')!) : null;
  if (of !== null && !STRUCTURE_ID.test(of)) throw new Problem(422, 'Name a structure by its id.');
  const { sql, values } = openShapesQuery(of, after);
  const [rows, named] = await Promise.all([env.ATLAS.prepare(sql).bind(...values, PAGE + 1).all<{ structure: string; label: string | null; subject: string; crop: number; open: number }>(),
    of === null ? null : env.ATLAS.prepare('SELECT id,label FROM structures WHERE id=?').bind(of).first<{ id: string; label: string }>()]);
  if (of !== null && !named) throw new Problem(404, 'No such structure.');
  const read = rows.results.slice(0, PAGE), last = read.at(-1), page = read.filter(row => row.open);
  const items = new Map((await listingItems(env, page.map(row => row.crop))).map(item => [item.id, item]));
  return { items: page.flatMap(row => { const item = items.get(publicId(row.crop)); return item ? [{ ...item, structure: { id: row.structure, label: row.label ?? '' } }] : [] }),
    next: rows.results.length > PAGE && last ? encodeCursor([last.structure, last.subject]) : null, structure: named };
}
