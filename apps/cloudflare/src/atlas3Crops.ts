// Crop ids at the URL boundary of the new storage layout: a route parameter to the integer crop id it
// names, an old crop URL to a redirect, and a write from a tab opened before the cutover to 409.
import { bundle, cropOf, pageOf, publicId, slotOf } from './atlas3';
import { aliasCrop } from './atlas3Aliases';

// An old crop URL answers 302 until the cutover is confirmed, then 301 (decision record 0006).
export const OLD_URL_STATUS: 301 | 302 = 302;
// How many retirements a link follows: a publication names the crop a retired one was retired in
// favour of, one hop, so a longer chain is a loop.
const REPLACEMENT_HOPS = 8;

// Whether a crop has a row, and the revision of its page, whose bundle holds a retired crop's entry.
// Two primary-key lookups.
export const standingQuery = () => `SELECT (SELECT 1 FROM crops WHERE id=?1) AS live,(SELECT rev FROM pages WHERE id=?2) AS rev`;

/** What a route parameter names: a live crop (`id`, the parameter being its public id as written),
 *  a live crop under another spelling or an old text id, or a crop retired in favour of it (`moved`),
 *  a retired crop that names no replacement, where a chain of replacements ends too (`retired`, with
 *  `canonical` when the parameter is that crop's public id as written), or nothing. */
export type CropParam = { id: number } | { moved: number } | { retired: number; canonical: boolean } | null;

// Every old text id has a colon (2,128,966 mirror ids and 7,200 redirect sources at the cutover) and no
// public id has one, so the two never meet.
const textId = (value: string) => value.includes(':');

async function replacement(env: Env, entry: Record<string, unknown> | undefined, ctx?: ExecutionContext): Promise<number | null> {
  const named = entry?.retired ? entry.replaced_by : undefined;
  if (typeof named === 'number') return named;
  return typeof named === 'string' && named ? await aliasCrop(env, named, ctx) : null;
}

/** A path segment as written in the URL, percent-decoded once; null when it does not decode. */
export function decodedSegment(raw: string): string | null {
  try { return decodeURIComponent(raw); } catch { return null; }
}

/** What the path segment `raw`, as written in the URL, names. It is decoded here and nowhere else. */
export async function cropParam(env: Env, raw: string, ctx?: ExecutionContext): Promise<CropParam> {
  const param = decodedSegment(raw);
  if (param === null) return null;
  const named = textId(param) ? await aliasCrop(env, param, ctx) : cropOf(param);
  if (named === null) return null;
  let id = named, replaced = false;
  for (let hop = 0; ; hop++) {
    const row = await env.ATLAS.prepare(standingQuery()).bind(id, pageOf(id)).first<{ live: number | null; rev: number | null }>();
    if (row?.live) break;
    if (row?.rev == null) return null;
    const entry = (await bundle(env, pageOf(id), row.rev))?.bySlot.get(slotOf(id));
    if (!entry?.retired) return null;
    if (hop >= REPLACEMENT_HOPS) return null;
    const next = await replacement(env, entry, ctx);
    if (next === null) return { retired: id, canonical: !replaced && publicId(id) === param };
    id = next; replaced = true;
  }
  return !replaced && publicId(id) === param ? { id } : { moved: id };
}

/** The URL with the crop's public id in place of the path segment `raw`, written as in the URL, and
 *  of any query value that is that segment decoded. */
export function cropUrl(url: URL, raw: string, id: number): URL {
  const target = new URL(url), name = publicId(id), param = decodedSegment(raw);
  target.pathname = target.pathname.split('/').map(part => part === raw ? name : part).join('/');
  for (const [key, value] of [...target.searchParams]) if (value === param) target.searchParams.set(key, name);
  return target;
}

export function movedResponse(url: URL, raw: string, id: number): Response {
  return new Response(null, { status: OLD_URL_STATUS, headers: { location: cropUrl(url, raw, id).href,
    'cache-control': OLD_URL_STATUS === 301 ? 'public, max-age=86400' : 'no-store' } });
}

// A write names crops by public id. An old text id comes from a tab opened before the cutover, or a
// save its outbox held through the freeze: it is read through the alias map, a closed set, and the
// crop's revision check refuses it should the crop have changed since. A text id the map does not hold
// is refused with a prompt to reload.
export const STALE_CODE = 'reload';
export const STALE_MESSAGE = 'This page is from before an update. Reload it.';
export class StaleCrop extends Error {
  constructor() { super(STALE_MESSAGE); }
}
/** The crop id in a crop field of a write body (the crop a review, mark or favourite is about), or
 *  null for a value that is no crop id. Throws StaleCrop for a text id the alias map does not hold.
 *  Only crop fields come here: a claim's `subject` is a book, form or word key kept as text, and a
 *  claim on a crop names the crop in its own `crop` field. */
export async function writtenCropField(env: Env, value: unknown, ctx?: ExecutionContext): Promise<number | null> {
  if (typeof value !== 'string') return null;
  if (!textId(value)) return cropOf(value);
  const id = await aliasCrop(env, value, ctx);
  if (id === null) throw new StaleCrop();
  return id;
}
export const staleResponse = (headers: Record<string, string> = {}) => Response.json(
  { detail: STALE_MESSAGE, code: STALE_CODE },
  { status: 409, headers: { ...headers, 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
