// The catalogue version cached reads are keyed by, on the new storage layout. Every write that changes
// what a listing shows moves one of the stamps:
// - a publication's page batch or the history load inserts its stamp into `imports`;
// - `answers.py` bumps `generation` (it moves `v3/pointer.json` after, but counts read before and after
//   the move are the same: the answers absorb exactly the tally rows they replace);
// - a review, undo or mark adds an event; a round adds a submission; an undo of a round with no review
//   only marks its submission undone;
// - a claim or claim action adds a ledger submission; a forms decision adds a row to `form_decisions`.
// The read is one statement of six index seeks: each max is the last key of its table, and the undone
// count reads `submission_undone`, which holds only undone submissions. No R2 read: the pointer is
// covered by `generation`.
// An isolate keeps the stamps for `CATALOGUE_LAG` seconds; a request from a browser that wrote in that
// time comes with the lag at 0 and reads them afresh, so a reviewer's own save shows at once.
import { held } from './held';

export type CatalogueStamps = { gen: number | null; import: number | null; event: number | null; submission: number | null;
  undone: number; ledger: number | null; decision: number | null };
export const stampsQuery = () => `SELECT (SELECT gen FROM generation WHERE id=0) AS gen,(SELECT max(rowid) FROM imports) AS import,
  (SELECT max(id) FROM events) AS event,(SELECT max(id) FROM submissions) AS submission,
  (SELECT count(*) FROM submissions WHERE undone=1) AS undone,(SELECT max(rowid) FROM ledger_submissions) AS ledger,
  (SELECT max(seq) FROM form_decisions) AS decision`;
const heldStamps = held<CatalogueStamps | null>();
export function catalogueStamps(env: Env, now = Date.now()): Promise<CatalogueStamps | null> {
  return heldStamps(() => env.ATLAS.prepare(stampsQuery()).first<CatalogueStamps>(), now, Number(env.CATALOGUE_LAG));
}
export const catalogueKey = (stamps: CatalogueStamps | null) => [stamps?.gen ?? 0, stamps?.import ?? 0, stamps?.event ?? 0,
  stamps?.submission ?? 0, stamps?.undone ?? 0, stamps?.ledger ?? 0, stamps?.decision ?? 0].join(':');
export async function catalogueVersion(env: Env, now = Date.now()): Promise<string> {
  return catalogueKey(await catalogueStamps(env, now));
}
// The publication the collection's crops come from, without the reviews since.
export const publicationKey = (stamps: CatalogueStamps | null) => [stamps?.gen ?? 0, stamps?.import ?? 0].join(':');
