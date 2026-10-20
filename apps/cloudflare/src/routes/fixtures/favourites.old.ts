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

/** One page of the user's starred crops as listing items, newest first. A retired crop is shown as
 *  the crop that replaced it, and one with no replacement is left out. */
export async function favouriteCrops(env: Env, user: string, offset: number, limit: number, itemsFor: Items, replacement: Replacement) {
  const [rows, total] = await Promise.all([
    env.DB.prepare('SELECT unit FROM favourites WHERE user_id=? ORDER BY at DESC, unit LIMIT ? OFFSET ?')
      .bind(user, Math.min(limit, PAGE), offset).all<{ unit: string }>(),
    env.DB.prepare('SELECT count(*) AS n FROM favourites WHERE user_id=?').bind(user).first<{ n: number }>(),
  ]);
  const ids = rows.results.map(r => r.unit);
  const found = await itemsFor(env, ids);
  const missing = ids.filter(id => !found.has(id));
  const moved = new Map<string, string>();
  for (const id of missing) { const target = await replacement(env, id); if (target) moved.set(id, target) }
  const replaced = await itemsFor(env, [...new Set(moved.values())]);
  const seen = new Set<string>(), items: Json[] = [];
  for (const id of ids) {
    const item = found.get(id) ?? replaced.get(moved.get(id) ?? '');
    if (item && !seen.has(item.id)) { seen.add(item.id); items.push(item) }
  }
  return { items, total: total?.n ?? 0, offset, next: offset + ids.length < (total?.n ?? 0) ? offset + ids.length : null };
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
  const held = await env.DB.prepare('SELECT count(*) AS n FROM favourites WHERE user_id=?').bind(user).first<{ n: number }>();
  if ((held?.n ?? 0) >= FAVOURITES_HELD) throw new FavouriteError(429, `You can star at most ${FAVOURITES_HELD} crops.`);
  await env.DB.prepare('INSERT OR IGNORE INTO favourites(user_id,unit,at) VALUES(?,?,?)').bind(user, crop, new Date().toISOString()).run();
  return { crop, favourite };
}
