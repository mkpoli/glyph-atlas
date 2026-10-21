// The crops a user has starred, in `favourites` in ATLAS. Any session may star one, an anonymous one
// too, and signing in to an account brings its stars along (auth.ts). Crops are integer ids in the
// table and public ids in requests and answers.
import { publicId } from '../atlas3';
import { cropQueryParam, writtenCropField } from '../atlas3Crops';

type Json = Record<string, any>;
// Listing items for crops by id, in the order asked; a crop that has none is left out.
export type Items = (env: Env, ids: number[]) => Promise<Json[]>;

// How many crops one user may star; the inspector reads every starred id at once.
export const FAVOURITES_HELD = 2000;
const PAGE = 60;

export class FavouriteError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

// Newest first, read along `favourite_newest`: no sort.
export const favouriteIdsQuery = () => 'SELECT crop FROM favourites WHERE user=? ORDER BY at DESC, crop LIMIT ?';
export const favouritePageQuery = () => 'SELECT crop FROM favourites WHERE user=? ORDER BY at DESC, crop LIMIT ? OFFSET ?';
export const favouriteCountQuery = () => 'SELECT count(*) AS n FROM favourites WHERE user=?';

/** Every crop the user has starred, newest first. */
export async function favouriteIds(env: Env, user: string) {
  const rows = await env.ATLAS.prepare(favouriteIdsQuery()).bind(user, FAVOURITES_HELD).all<{ crop: number }>();
  return { ids: rows.results.map(r => publicId(r.crop)) };
}

/** One page of the user's starred crops as listing items, newest first. A retired crop is shown as
 *  the crop that replaced it, and one with no replacement is left out. */
export async function favouriteCrops(env: Env, user: string, offset: number, limit: number, items: Items) {
  const [rows, total] = await env.ATLAS.batch<{ crop: number } | { n: number }>([
    env.ATLAS.prepare(favouritePageQuery()).bind(user, Math.min(limit, PAGE), offset),
    env.ATLAS.prepare(favouriteCountQuery()).bind(user),
  ]);
  const ids = (rows.results as { crop: number }[]).map(r => r.crop), n = (total.results[0] as { n: number } | undefined)?.n ?? 0;
  const found = new Map((await items(env, ids)).map(item => [item.id as string, item]));
  const moved = new Map<number, number>();
  for (const id of ids) {
    if (found.has(publicId(id))) continue;
    const named = await cropQueryParam(env, publicId(id));
    if (named && 'moved' in named) moved.set(id, named.moved);
  }
  const replaced = new Map((await items(env, [...new Set(moved.values())])).map(item => [item.id as string, item]));
  const seen = new Set<string>(), out: Json[] = [];
  for (const id of ids) {
    const item = found.get(publicId(id)) ?? (moved.has(id) ? replaced.get(publicId(moved.get(id)!)) : undefined);
    if (item && !seen.has(item.id)) { seen.add(item.id); out.push(item) }
  }
  return { items: out, total: n, offset, next: offset + ids.length < n ? offset + ids.length : null };
}

/** Star a crop or take its star away. `crop` is a public id, or an old text id from a page opened
 *  before the cutover (StaleCrop when the alias map does not hold it). */
export async function setFavourite(env: Env, user: string, input: Json, ctx?: ExecutionContext) {
  const crop = input.crop, favourite = input.favourite;
  if (typeof crop !== 'string' || !crop || crop.length > 256) throw new FavouriteError(422, 'Name the crop to star.');
  if (typeof favourite !== 'boolean') throw new FavouriteError(422, 'Say whether the crop is starred.');
  const id = await writtenCropField(env, crop, ctx);
  const named = id === null ? crop : publicId(id);
  if (!favourite) {
    if (id !== null) await env.ATLAS.prepare('DELETE FROM favourites WHERE user=? AND crop=?').bind(user, id).run();
    return { crop: named, favourite };
  }
  // A crop with a row is published; a retired one has none.
  const [known, held] = id === null ? [null, null] : await env.ATLAS.batch<{ n: number }>([
    env.ATLAS.prepare('SELECT 1 AS n FROM crops WHERE id=?').bind(id),
    env.ATLAS.prepare(favouriteCountQuery()).bind(user),
  ]);
  if (!known?.results.length) throw new FavouriteError(404, 'This crop is not in the published collection.');
  if ((held!.results[0]?.n ?? 0) >= FAVOURITES_HELD) throw new FavouriteError(429, `You can star at most ${FAVOURITES_HELD} crops.`);
  await env.ATLAS.prepare('INSERT OR IGNORE INTO favourites(user,crop,at) VALUES(?,?,?)').bind(user, id, Date.now()).run();
  return { crop: named, favourite };
}
