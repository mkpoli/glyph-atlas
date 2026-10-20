// Who has decided the most. Each decision that still stands counts once: every crop a round, a
// correction or a single review judged (an undone or rejected submission drops out), every archived
// written form, every form decision, every claim a person made in the ledger that they have not taken
// back, and each claim they accepted, rejected or adjudicated, once however often. A claim counts once
// however many members or rows its submission wrote. A person's claims are the ones a submission
// wrote; a source's or a model's (written by a publication, with none), one moved from the written
// forms (counted as their archived row), and the claim naming a form that a first choice writes
// beside it count as nobody's. A form chosen as
// another member of the crop's grapheme also renames the crop by a review, which its claim names
// (`input.review`), and counts once, as that review. A user counts the work of every id they hold; an
// old reviewer id nobody holds counts on its own.
//
// Reviews are each actor's running total in `actor_stats`, which every review batch and undo keeps, so
// the count survives the events D1 lets go after 90 days. The ledger, form decisions and written forms
// name their actor by its text (`actors.key`). Names come from ACCOUNTS (accounts.ts).
import { people } from '../accounts';

type Json = Record<string, any>;
const LIMIT = 100;
// The ranking changes slowly, and each reading counts the whole ledger, so the edge keeps it a while.
const TTL = 300;

export const rankingQuery = () => `WITH work(actor,n) AS (
    SELECT a.key,t.reviews FROM actor_stats t JOIN actors a ON a.id=t.actor WHERE t.reviews>0
    UNION ALL SELECT actor,count(*) FROM written_forms GROUP BY actor
    UNION ALL SELECT actor,count(*) FROM form_decisions GROUP BY actor
    UNION ALL SELECT a.asserted_by,count(DISTINCT a.submission) FROM assertions a INDEXED BY assertion_person
      LEFT JOIN ledger_submissions l ON l.id=a.submission
      WHERE a.submission IS NOT NULL AND a.legacy IS NULL AND a.predicate<>'represented_by'
      AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
      AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.key=l.actor||':'||json_extract(l.request,'$.input.review') AND s.undone=0)
      GROUP BY a.asserted_by
    UNION ALL SELECT actor,count(DISTINCT assertion) FROM assertion_actions WHERE action IN ('accept','reject','adjudicate') GROUP BY actor),
  who(key,user,actor,n) AS (SELECT coalesce(a.user,'actor:'||w.actor),a.user,w.actor,w.n FROM work w LEFT JOIN actors a ON a.key=w.actor)
  SELECT key,user,min(actor) AS actor,sum(n) AS total FROM who GROUP BY key ORDER BY total DESC,key LIMIT ${LIMIT}`;

/** A ranked row as the page shows it: a signed-in user by name, anyone else as anonymous with their id. */
export function rankingRow(row: Json, place: number): Json {
  const named = Boolean(row.user) && !row.anonymous;
  return { place, total: row.total, user: row.user ?? null, anonymous: !named,
    name: named ? row.name : row.user ? row.name : row.actor, image: named ? row.image : null };
}

/** The ranked rows with each user's name, picture and whether it is anonymous; a user ACCOUNTS no longer
 *  holds has none. */
export async function rankingRows(env: Env): Promise<Json[]> {
  const rows = (await env.ATLAS.prepare(rankingQuery()).all<Json>()).results;
  const named = await people(env, rows.map(row => row.user));
  return rows.map((row, i) => {
    const person = row.user ? named.get(row.user) : undefined;
    return rankingRow({ ...row, name: person?.name ?? null, image: person?.image ?? null, anonymous: person?.anonymous ? 1 : 0 }, i + 1);
  });
}

export async function ranking(env: Env, url: URL, ctx: ExecutionContext): Promise<Response> {
  const key = new Request(`${url.origin}/api/ranking`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const response = Response.json({ items: await rankingRows(env), at: new Date().toISOString() },
    { headers: { 'cache-control': `public, max-age=${TTL}` } });
  ctx.waitUntil(caches.default.put(key, response.clone()));
  return response;
}
