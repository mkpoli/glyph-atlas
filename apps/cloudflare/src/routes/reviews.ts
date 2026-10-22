// The review export: every review, oldest first, as the offline importer reads it. A review saved on this
// site returns the record its submission froze in R2 (`reviewCapture.ts`); one moved from the old site
// returns the old export's record, archived by page. `current` and `current_revision` count revisions as
// the old site and the offline store do: a crop's revision less its marks.
import { publicId } from '../atlas3';
import { Problem } from '../busy';
import { readLegacyReviews, readReviewEvidence } from '../reviewEvidence';
import { MARKED } from './review';

type Json = Record<string, any>;
export const PAGE = 200;
export const reviewsQuery = () => `SELECT e.id,e.legacy,e.crop,e.at,s.evidence,
  c.revision-(SELECT count(*) FROM events m WHERE m.crop=e.crop AND ${MARKED('m')}) AS current_revision,
  EXISTS(SELECT 1 FROM events n WHERE n.crop=e.crop AND n.revision>e.revision AND NOT ${MARKED('n')}) AS superseded
  FROM events e INDEXED BY event_history JOIN submissions s ON s.id=e.submission LEFT JOIN crops c ON c.id=e.crop
  WHERE e.kind IN (0,1,2) AND (e.at,e.id)>(?1,?2) ORDER BY e.at,e.id LIMIT ?3`;
type Row = { id: number; legacy: string | null; crop: number; at: number; evidence: string | null;
  current_revision: number | null; superseded: number };

/** The cursor of a page: the (at, id) of its last review. */
export const encodeCursor = (at: number, id: number) => btoa(JSON.stringify([at, id]));
export function decodeCursor(value: string | null): { at: number; id: number } {
  if (!value) return { at: -1, id: 0 };
  try {
    const d = JSON.parse(atob(value));
    if (Array.isArray(d) && d.length === 2 && d.every(Number.isSafeInteger)) return { at: d[0], id: d[1] };
  } catch { /* refused below */ }
  throw new Problem(422, 'Invalid cursor.');
}

/** One page of the export. A missing or damaged object fails the whole page, so no partial page is read
 *  as complete. A review saved here before its evidence was kept is listed without it (`evidence: null`). */
export async function reviewsPage(env: Env, q: URLSearchParams) {
  const after = decodeCursor(q.get('after'));
  const [rows, published] = await env.ATLAS.batch<Json>([
    env.ATLAS.prepare(reviewsQuery()).bind(after.at, after.id, PAGE + 1),
    env.ATLAS.prepare('SELECT at FROM imports WHERE rowid=(SELECT max(rowid) FROM imports)'),
  ]);
  const page = (rows.results as Row[]).slice(0, PAGE);
  // One object per submission or archived page, read eight at a time; only the records this page needs are kept.
  const sources = new Map<string, Row[]>();
  for (const row of page) {
    const source = row.legacy !== null ? `page:${row.crop >> 24}` : row.evidence ? `hash:${row.evidence}` : null;
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
  const reviews: Json[] = [];
  for (const row of page) {
    const state = { crop: publicId(row.crop), current: !row.superseded, current_revision: row.current_revision };
    const stored = selected.get(row.id);
    if (!stored) {
      reviews.push({ event: { id: `v3:${row.id}`, target_type: 'unit', field: 'review', at: new Date(row.at).toISOString() },
        evidence: null, ...state });
      continue;
    }
    // The old export left out reviews of crops it no longer held; their records stay for audit.
    if (stored.legacy_hidden) continue;
    reviews.push({ ...stored, event: row.legacy === null ? { ...stored.event, id: `v3:${row.id}` } : stored.event, ...state });
  }
  const last = rows.results.length > PAGE ? page[PAGE - 1] : null;
  const at = (published.results[0] as { at: number } | undefined)?.at;
  return { version: 1, kind: 'atlas-character-reviews', reviews, publication: at ? new Date(at * 1000).toISOString() : null,
    next: last ? encodeCursor(last.at, last.id) : null };
}
