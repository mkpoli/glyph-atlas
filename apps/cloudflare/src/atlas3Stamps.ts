// The catalogue version cached reads are keyed by, on the new storage layout. Every write that changes
// what a listing shows moves one of the stamps:
// - a publication's page batch or the history load inserts its stamp into `imports`;
// - `answers.py` bumps `generation` (it moves `v3/pointer.json` after, but counts read before and after
//   the move are the same: the answers absorb exactly the tally rows they replace);
// - a review, undo or mark adds an event; a round adds a submission; an undo that marks its submission
//   undone counts itself in `site` under `undos` (`undoCountQuery`), all that moves when the round
//   held no review;
// - a claim or claim action adds a ledger submission; a forms decision adds a row to `form_decisions`.
// The read is one statement of seven key seeks: each max is the last key of its table, and the rest are
// one row each by its key. No R2 read: the pointer is covered by `generation`.
// An isolate keeps the stamps for `CATALOGUE_LAG` seconds; a request from a browser that wrote in that
// time comes with the lag at 0 and reads them afresh, so a reviewer's own save shows at once.
import { held } from './held';

export type CatalogueStamps = { gen: number | null; import: number | null; event: number | null; submission: number | null;
  undos: number | null; ledger: number | null; decision: number | null };
export const stampsQuery = () => `SELECT (SELECT gen FROM generation WHERE id=0) AS gen,(SELECT max(rowid) FROM imports) AS import,
  (SELECT max(id) FROM events) AS event,(SELECT max(id) FROM submissions) AS submission,
  (SELECT CAST(value AS INTEGER) FROM site WHERE key='undos') AS undos,(SELECT max(rowid) FROM ledger_submissions) AS ledger,
  (SELECT max(seq) FROM form_decisions) AS decision`;
// One more undo that marked its submission undone, written in the batch that marks it: the batch fails
// whole when another request undid the submission first, so each is counted once.
export const undoCountQuery = () => `INSERT INTO site(key,value) VALUES('undos','1')
  ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1`;
const heldStamps = held<CatalogueStamps | null>();
export function catalogueStamps(env: Env, now = Date.now()): Promise<CatalogueStamps | null> {
  return heldStamps(() => env.ATLAS.prepare(stampsQuery()).first<CatalogueStamps>(), now, Number(env.CATALOGUE_LAG));
}
export const catalogueKey = (stamps: CatalogueStamps | null) => [stamps?.gen ?? 0, stamps?.import ?? 0, stamps?.event ?? 0,
  stamps?.submission ?? 0, stamps?.undos ?? 0, stamps?.ledger ?? 0, stamps?.decision ?? 0].join(':');
export async function catalogueVersion(env: Env, now = Date.now()): Promise<string> {
  return catalogueKey(await catalogueStamps(env, now));
}
// The publication the collection's crops come from, without the reviews since.
export const publicationKey = (stamps: CatalogueStamps | null) => [stamps?.gen ?? 0, stamps?.import ?? 0].join(':');
