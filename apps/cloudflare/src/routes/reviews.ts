// The review export: every review, oldest first, as the offline importer reads it. A review saved on this
// site returns the record its submission froze in R2 (`reviewCapture.ts`); one moved from the old site
// returns the old export's record, archived by page. `current_revision` counts revisions as the old site
// and the offline store do (`LOGICAL`); a review is current until a later event on its crop but a mark.
import { bundle, pageOf, publicId, slotOf } from '../atlas3';
import { Problem } from '../busy';
import { readLegacyReviews, readReviewEvidence } from '../reviewEvidence';
import { LOGICAL, MARKED } from './review';

type Json = Record<string, any>;
export const PAGE = 200;
export const reviewsQuery = () => `SELECT e.id,e.legacy,e.crop,e.at,s.evidence,
  CASE WHEN c.id IS NULL THEN NULL ELSE ${LOGICAL('c')} END AS current_revision,
  EXISTS(SELECT 1 FROM events n WHERE n.crop=e.crop AND n.revision>e.revision AND NOT ${MARKED('n')}) AS superseded
  FROM events e INDEXED BY event_reviews JOIN submissions s ON s.id=e.submission LEFT JOIN crops c ON c.id=e.crop
  WHERE e.kind IN (0,1,2) AND e.id>?1 ORDER BY e.id LIMIT ?2`;
type Row = { id: number; legacy: string | null; crop: number; at: number; evidence: string | null;
  current_revision: number | null; superseded: number };

/** The cursor of a page: the id of its last review. Ids follow the order reviews committed in, so a
 *  review committed while the export is read lands after the cursor. */
export const encodeCursor = (id: number) => btoa(JSON.stringify([id]));
export function decodeCursor(value: string | null): { id: number } {
  if (!value) return { id: 0 };
  try {
    const d = JSON.parse(atob(value));
    if (Array.isArray(d) && d.length === 1 && Number.isSafeInteger(d[0])) return { id: d[0] };
  } catch { /* refused below */ }
  throw new Problem(422, 'Invalid cursor.');
}

/** One page of the export. A missing or damaged object fails the whole page, so no partial page is read
 *  as complete. A review saved here before its evidence was kept is listed without it (`evidence: null`). */
export async function reviewsPage(env: Env, q: URLSearchParams) {
  const after = decodeCursor(q.get('after'));
  const [rows, published] = await env.ATLAS.batch<Json>([
    env.ATLAS.prepare(reviewsQuery()).bind(after.id, PAGE + 1),
    env.ATLAS.prepare('SELECT at FROM imports WHERE rowid=(SELECT max(rowid) FROM imports)'),
  ]);
  const page = (rows.results as Row[]).slice(0, PAGE);
  // One object per submission or archived page, read eight at a time; only the records this page needs are kept.
  const sources = new Map<string, Row[]>();
  for (const row of page) {
    const source = row.legacy !== null ? `page:${pageOf(row.crop)}` : row.evidence ? `hash:${row.evidence}` : null;
    if (source) sources.set(source, [...(sources.get(source) ?? []), row]);
  }
  const selected = new Map<number, Json>(), groups = [...sources];
  for (let offset = 0; offset < groups.length; offset += 8) {
    await Promise.all(groups.slice(offset, offset + 8).map(async ([source, rows]) => {
      const object = source.startsWith('page:') ? await readLegacyReviews(env, Number(source.slice(5)))
        : await readReviewEvidence(env, source.slice(5));
      if (!object) throw new Problem(503, 'The reviews moved from the old site are not archived yet.');
      for (const row of rows) {
        const stored = object[row.legacy === null ? `crop:${row.crop}` : `legacy:${row.legacy}`];
        if (!stored) throw new Problem(503, 'A review is missing from its evidence object.');
        selected.set(row.id, stored);
      }
    }));
  }
  // A review without its record names its crop by the source id its page bundle holds.
  const bare = page.filter(row => row.legacy === null && !row.evidence);
  const revs = bare.length ? (await env.ATLAS.prepare('SELECT p.id,p.rev FROM json_each(?) j JOIN pages p ON p.id=j.value')
    .bind(JSON.stringify([...new Set(bare.map(row => pageOf(row.crop)))])).all<{ id: number; rev: number }>()).results : [];
  const bundles = new Map(await Promise.all(revs.map(async p => [p.id, await bundle(env, p.id, p.rev)] as const)));
  const reviews: Json[] = [];
  for (const row of page) {
    const state = { crop: publicId(row.crop), current: !row.superseded, current_revision: row.current_revision };
    const stored = selected.get(row.id);
    if (!stored) {
      // Saved before this site kept evidence: what the reviewer saw is not recorded, so it cannot be imported.
      reviews.push({ event: { id: `v3:${row.id}`, target_type: 'unit', target_id: bundles.get(pageOf(row.crop))?.bySlot.get(slotOf(row.crop))?.id ?? publicId(row.crop), field: 'review',
        at: new Date(row.at).toISOString() }, evidence: null, unavailable: 'saved before review evidence was kept', ...state });
      continue;
    }
    // The old export left out reviews of crops it no longer held; their records stay for audit.
    if (stored.legacy_hidden) continue;
    reviews.push({ ...stored, event: row.legacy === null ? { ...stored.event, id: `v3:${row.id}` } : stored.event, ...state });
  }
  const last = rows.results.length > PAGE ? page[PAGE - 1] : null;
  const at = (published.results[0] as { at: number } | undefined)?.at;
  return { version: 1, kind: 'atlas-character-reviews', reviews, publication: at ? new Date(at * 1000).toISOString() : null,
    next: last ? encodeCursor(last.id) : null };
}
