// Users live in glyph-atlas-accounts (`ACCOUNTS`); what they wrote, their actors and their favourites in
// glyph-atlas-v3 (`ATLAS`). D1 cannot join across databases, so a page naming who wrote a row reads the
// ids from one database and the names from the other, a bounded IN list to a statement.

// D1 binds at most 100 values to a statement.
export const IN_LIST = 100;
// The most ids one request may look up: a page of rows names far fewer.
export const MAX_IDS = 1000;

export type Person = { name: string; image: string | null };
export type ActorPerson = { user: string | null; name: string | null; image: string | null };

function groups<T>(values: Iterable<T | null | undefined>): T[][] {
  const unique = [...new Set([...values].filter((value): value is T => value !== null && value !== undefined))];
  if (unique.length > MAX_IDS) throw new RangeError(`${unique.length} ids in one lookup; at most ${MAX_IDS}.`);
  const out: T[][] = [];
  for (let i = 0; i < unique.length; i += IN_LIST) out.push(unique.slice(i, i + IN_LIST));
  return out;
}
const marks = (n: number) => Array(n).fill('?').join(',');

/** The name and picture of each user in `ids` that still has an account. */
export async function people(env: Env, ids: Iterable<string | null | undefined>): Promise<Map<string, Person>> {
  const parts = groups(ids);
  const out = new Map<string, Person>();
  if (!parts.length) return out;
  const results = await env.ACCOUNTS.batch<{ id: string; name: string; image: string | null }>(
    parts.map(part => env.ACCOUNTS.prepare(`SELECT id,name,image FROM "user" WHERE id IN (${marks(part.length)})`).bind(...part)));
  for (const { results: rows } of results) for (const row of rows) out.set(row.id, { name: row.name, image: row.image });
  return out;
}

/** The user each actor in `actors` belongs to, with that user's name and picture. */
export async function actorPeople(env: Env, actors: Iterable<number | null | undefined>): Promise<Map<number, ActorPerson>> {
  const parts = groups(actors);
  const out = new Map<number, ActorPerson>();
  if (!parts.length) return out;
  const results = await env.ATLAS.batch<{ id: number; user: string | null }>(
    parts.map(part => env.ATLAS.prepare(`SELECT id,user FROM actors WHERE id IN (${marks(part.length)})`).bind(...part)));
  const owners = results.flatMap(result => result.results);
  const named = await people(env, owners.map(row => row.user));
  for (const { id, user } of owners) {
    const person = user ? named.get(user) : undefined;
    out.set(id, { user, name: person?.name ?? null, image: person?.image ?? null });
  }
  return out;
}

// Unfinished deletions taken up at once.
const RESUME = 20;

/** Mark `user` as being deleted, before its rows in `ACCOUNTS` go. */
export async function startForgetting(env: Env, user: string) {
  await env.ACCOUNTS.prepare('INSERT OR IGNORE INTO forgotten(user,at) VALUES(?,?)').bind(user, new Date().toISOString()).run();
}

/**
 * Finish the deletion of every user marked as being deleted, a few at a time: their favourites and their
 * links to actors in `ATLAS` go, then the mark. The actors stay, so the history keeps its rows, unnamed.
 * Running it again after a failure repeats only what is left.
 */
export async function finishForgetting(env: Env) {
  const marked = (await env.ACCOUNTS.prepare('SELECT user,at FROM forgotten ORDER BY at,user LIMIT ?').bind(RESUME).all<{ user: string; at: string }>()).results;
  const live = await people(env, marked.map(row => row.user));
  const gone = marked.filter(row => !live.has(row.user)).map(row => row.user);
  // A user still present after an hour was not deleted after all; a newer mark may be a deletion under way.
  const stale = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const done = [...gone, ...marked.filter(row => live.has(row.user) && row.at < stale).map(row => row.user)];
  if (gone.length) await env.ATLAS.batch(gone.flatMap(user => [
    env.ATLAS.prepare('DELETE FROM favourites WHERE user=?').bind(user),
    env.ATLAS.prepare('UPDATE actors SET user=NULL WHERE user=?').bind(user),
  ]));
  if (done.length) await env.ACCOUNTS.prepare(`DELETE FROM forgotten WHERE user IN (${marks(done.length)})`).bind(...done).run();
  return gone;
}
