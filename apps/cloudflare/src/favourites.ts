// The crops a user has starred. Any session may star one, an anonymous one too, and signing in to an
// account brings its stars along (auth.ts).
type Json = Record<string, any>;
type Items = (env: Env, ids: string[]) => Promise<Map<string, Json>>;
type Replacement = (env: Env, id: string) => Promise<string | null>;

// How many crops one user may star; the inspector reads every starred id at once.
export const FAVOURITES_HELD = 2000;
const PAGE = 60;

export class FavouriteError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

/** Every crop the user has starred, newest first. */
export async function favouriteIds(env: Env, user: string) {
  const rows = await env.DB.prepare('SELECT unit FROM favourites WHERE user_id=? ORDER BY at DESC, unit LIMIT ?')
    .bind(user, FAVOURITES_HELD).all<{ unit: string }>();
  return { ids: rows.results.map(r => r.unit) };
}

/** A page's place in the list: the time and crop of the last row it read. */
const cursorOf = (row: { at: string; unit: string }) => `${row.at}|${row.unit}`;
function parseCursor(cursor: string | null) {
  if (!cursor) return null;
  const at = cursor.indexOf('|');
  if (at < 1) throw new FavouriteError(422, 'Not a place in the favourites list.');
  return { at: cursor.slice(0, at), unit: cursor.slice(at + 1) };
}

/** One page of the user's starred crops as listing items, newest first, after `cursor`. A star on a
 *  retired crop moves to the crop that replaced it; one with no replacement is left out. */
export async function favouriteCrops(env: Env, user: string, cursor: string | null, limit: number, itemsFor: Items, replacement: Replacement) {
  const after = parseCursor(cursor);
  const rows = (await (after
    ? env.DB.prepare('SELECT unit,at FROM favourites WHERE user_id=? AND (at<? OR (at=? AND unit>?)) ORDER BY at DESC, unit LIMIT ?')
      .bind(user, after.at, after.at, after.unit, Math.min(limit, PAGE))
    : env.DB.prepare('SELECT unit,at FROM favourites WHERE user_id=? ORDER BY at DESC, unit LIMIT ?')
      .bind(user, Math.min(limit, PAGE))).all<{ unit: string; at: string }>()).results;
  const ids = rows.map(r => r.unit);
  const found = await itemsFor(env, ids);
  const moved = new Map<string, string>();
  for (const id of ids.filter(id => !found.has(id))) { const target = await replacement(env, id); if (target) moved.set(id, target) }
  if (moved.size) {
    await env.DB.batch([...moved].flatMap(([from, to]) => [
      env.DB.prepare('UPDATE OR IGNORE favourites SET unit=? WHERE user_id=? AND unit=?').bind(to, user, from),
      env.DB.prepare('DELETE FROM favourites WHERE user_id=? AND unit=?').bind(user, from),
    ]));
  }
  const replaced = await itemsFor(env, [...new Set(moved.values())]);
  const seen = new Set<string>(), items: Json[] = [];
  for (const id of ids) {
    const item = found.get(id) ?? replaced.get(moved.get(id) ?? '');
    if (item && !seen.has(item.id)) { seen.add(item.id); items.push(item) }
  }
  const total = await env.DB.prepare('SELECT count(*) AS n FROM favourites WHERE user_id=?').bind(user).first<{ n: number }>();
  return { items, total: total?.n ?? 0, moved: moved.size > 0, next: rows.length === Math.min(limit, PAGE) ? cursorOf(rows[rows.length - 1]) : null };
}

/** Star a crop or take its star away. */
export async function setFavourite(env: Env, user: string, input: Json) {
  const crop = input.crop, favourite = input.favourite;
  if (typeof crop !== 'string' || !crop || crop.length > 256) throw new FavouriteError(422, 'Name the crop to star.');
  if (typeof favourite !== 'boolean') throw new FavouriteError(422, 'Say whether the crop is starred.');
  if (!favourite) {
    await env.DB.prepare('DELETE FROM favourites WHERE user_id=? AND unit=?').bind(user, crop).run();
    return { crop, favourite };
  }
  const known = await env.DB.prepare(`SELECT 1 FROM units WHERE id=? AND origin!='retired' UNION ALL SELECT 1 FROM corpus_units WHERE id=? LIMIT 1`)
    .bind(crop, crop).first();
  if (!known) throw new FavouriteError(404, 'This crop is not in the published collection.');
  // The count is read in the insert itself, so two stars at once cannot both pass it.
  const { meta } = await env.DB.prepare(`INSERT OR IGNORE INTO favourites(user_id,unit,at) SELECT ?,?,?
    WHERE (SELECT count(*) FROM favourites WHERE user_id=?)<? OR EXISTS(SELECT 1 FROM favourites WHERE user_id=? AND unit=?)`)
    .bind(user, crop, new Date().toISOString(), user, FAVOURITES_HELD, user, crop).run();
  if (!meta.changes && !(await env.DB.prepare('SELECT 1 FROM favourites WHERE user_id=? AND unit=?').bind(user, crop).first()))
    throw new FavouriteError(429, `You can star at most ${FAVOURITES_HELD} crops.`);
  return { crop, favourite };
}

/** Move an anonymous user's stars to the account it signed in to, newest first, up to the limit; a crop
 *  the account had starred already keeps the account's own. */
export function carryFavourites(env: Env, from: string, to: string) {
  return env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO favourites(user_id,unit,at) SELECT ?,unit,at FROM favourites WHERE user_id=?
      AND unit NOT IN (SELECT unit FROM favourites WHERE user_id=?) ORDER BY at DESC
      LIMIT max(0,?-(SELECT count(*) FROM favourites WHERE user_id=?))`).bind(to, from, to, FAVOURITES_HELD, to),
    env.DB.prepare('DELETE FROM favourites WHERE user_id=?').bind(from),
  ]);
}
