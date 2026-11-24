// The crops given a 構形 and no form (`built_as`, docs/design/form-model.md): those whose shape waits to
// be named. A crop leaves the list when a form is claimed on it, or its structure taken back.
import { publicId } from '../atlas3';
import { listingItems } from '../atlas3Listing';
import { Problem } from '../busy';

type Json = Record<string, any>;
const FILED = `('asserted','accepted','adjudicated')`;
// A claim holds on the crop's effective cut; a form people disagree about is a form.
const OPEN = `c.predicate='built_as' AND c.scope='' AND c.slot='' AND c.status IN ${FILED} AND k.kind<2 AND c.cut=k.cut+(r.x IS NOT NULL)
    AND NOT EXISTS(SELECT 1 FROM current_claims f WHERE f.crop=c.crop AND f.predicate='has_form' AND f.cut=c.cut AND f.status<>'rejected')`;
const FROM = 'FROM current_claims c CROSS JOIN crops k ON k.id=c.crop LEFT JOIN reviews r ON r.crop=k.id';
/** A page of them, by structure and then by crop: one range of `current_claim_object`. `of` narrows it to
 *  one structure, and `after` is the structure and subject the last page ended at. */
export function openShapesQuery(of: string | null, after: [string, string] | null): { sql: string; values: string[] } {
  const values: string[] = [], bind = (value: string) => `?${values.push(value)}`;
  const where = [OPEN, of === null ? 'c.object IS NOT NULL' : `c.object=${bind(of)}`,
    ...(after ? [of === null ? `(c.object,c.subject)>(${bind(after[0])},${bind(after[1])})` : `c.subject>${bind(after[1])}`] : [])];
  return { sql: `SELECT c.object AS structure,s.label,c.subject,c.crop ${FROM} LEFT JOIN structures s ON s.id=c.object
  WHERE ${where.join(' AND ')} ORDER BY c.object,c.subject LIMIT ?${values.length + 1}`, values };
}
/** How many of these structures' crops wait for a form, by structure; one with none is left out. */
export const openShapeCountsQuery = () => `SELECT c.object AS structure,count(*) AS n ${FROM}
  WHERE c.object IN (SELECT value FROM json_each(?)) AND ${OPEN} GROUP BY c.object`;
export async function openShapeCounts(env: Env, structures: string[]): Promise<Map<string, number>> {
  if (!structures.length) return new Map();
  const rows = (await env.ATLAS.prepare(openShapeCountsQuery()).bind(JSON.stringify(structures)).all<{ structure: string; n: number }>()).results;
  return new Map(rows.map(row => [row.structure, row.n]));
}

const STRUCTURE_ID = /^st:[0-9a-f]{32}$/, LIMIT = 60;
const encodeCursor = (after: [string, string]) => btoa(JSON.stringify(after));
function decodeCursor(value: string): [string, string] {
  try {
    const found = JSON.parse(atob(value));
    if (!Array.isArray(found) || found.length !== 2 || !found.every(part => typeof part === 'string' && part.length <= 256)) throw 0;
    return found as [string, string];
  } catch { throw new Problem(422, 'Invalid cursor.') }
}
/** `{ items, next, structure }`: listing items, each with its `structure` `{ id, label }`, and where the next
 *  page starts; `structure` is the one the list is narrowed to. */
export async function openShapes(env: Env, q: URLSearchParams): Promise<Json> {
  const of = q.get('structure') || null, after = q.get('after') ? decodeCursor(q.get('after')!) : null;
  if (of !== null && !STRUCTURE_ID.test(of)) throw new Problem(422, 'Name a structure by its id.');
  const { sql, values } = openShapesQuery(of, after);
  const [rows, named] = await Promise.all([env.ATLAS.prepare(sql).bind(...values, LIMIT + 1).all<{ structure: string; label: string | null; subject: string; crop: number }>(),
    of === null ? null : env.ATLAS.prepare('SELECT id,label FROM structures WHERE id=?').bind(of).first<{ id: string; label: string }>()]);
  if (of !== null && !named) throw new Problem(404, 'No such structure.');
  const page = rows.results.slice(0, LIMIT), last = page.at(-1);
  const items = new Map((await listingItems(env, page.map(row => row.crop))).map(item => [item.id, item]));
  return { items: page.flatMap(row => { const item = items.get(publicId(row.crop)); return item ? [{ ...item, structure: { id: row.structure, label: row.label ?? '' } }] : [] }),
    next: rows.results.length > LIMIT && last ? encodeCursor([last.structure, last.subject]) : null, structure: named };
}
