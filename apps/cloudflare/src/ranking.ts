// Who has decided the most. Each decision that still stands counts once: every crop a round, a
// correction or a single review judged (an undone or rejected submission drops out), every written
// form, and every form decision. A user counts the work of every id they hold; an old reviewer id
// nobody holds counts on its own.
type Json = Record<string, any>;
const LIMIT = 100;
// The ranking changes slowly, and each reading counts the whole journal, so the edge keeps it a while.
const TTL = 300;

export const rankingQuery = () => `WITH work(actor,n) AS (
    SELECT e.actor,count(*) FROM events e JOIN submissions s ON s.id=e.submission WHERE e.kind='review' AND s.undone=0 GROUP BY e.actor
    UNION ALL SELECT actor,count(*) FROM written_forms GROUP BY actor
    UNION ALL SELECT actor,count(*) FROM form_decisions GROUP BY actor),
  who(key,user,actor,n) AS (SELECT coalesce(a.user_id,'actor:'||w.actor),a.user_id,w.actor,w.n FROM work w LEFT JOIN actors a ON a.actor=w.actor)
  SELECT key,user,min(actor) AS actor,sum(n) AS total,u.name,u.image,coalesce(u.isAnonymous,0) AS anonymous
  FROM who LEFT JOIN "user" u ON u.id=who.user GROUP BY key ORDER BY total DESC,key LIMIT ${LIMIT}`;

/** A ranked row as the page shows it: a signed-in user by name, anyone else as anonymous with their id. */
export function rankingRow(row: Json, place: number): Json {
  const named = Boolean(row.user) && !row.anonymous;
  return { place, total: row.total, user: row.user ?? null, anonymous: !named,
    name: named ? row.name : row.user ? row.name : row.actor, image: named ? row.image : null };
}

export async function ranking(env: Env, url: URL, ctx: ExecutionContext): Promise<Response> {
  const key = new Request(`${url.origin}/api/ranking`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const rows = (await env.DB.prepare(rankingQuery()).all<Json>()).results;
  const response = Response.json({ items: rows.map((row, i) => rankingRow(row, i + 1)), at: new Date().toISOString() },
    { headers: { 'cache-control': `public, max-age=${TTL}` } });
  ctx.waitUntil(caches.default.put(key, response.clone()));
  return response;
}
