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
 *  a retired crop that names no replacement (`retired`), or nothing. */
export type CropParam = { id: number } | { moved: number } | { retired: number } | null;

// Every old text id has a colon (2,128,966 mirror ids and 7,200 redirect sources at the cutover) and no
// public id has one, so the two never meet.
const textId = (value: string) => value.includes(':');

async function replacement(env: Env, entry: Record<string, unknown> | undefined, ctx?: ExecutionContext): Promise<number | null> {
  const named = entry?.retired ? entry.replaced_by : undefined;
  if (typeof named === 'number') return named;
  return typeof named === 'string' && named ? await aliasCrop(env, named, ctx) : null;
}

export async function cropParam(env: Env, param: string, ctx?: ExecutionContext): Promise<CropParam> {
  const named = textId(param) ? await aliasCrop(env, param, ctx) : cropOf(param);
  if (named === null) return null;
  let id = named, replaced = false;
  for (let hop = 0; ; hop++) {
    const row = await env.ATLAS.prepare(standingQuery()).bind(id, pageOf(id)).first<{ live: number | null; rev: number | null }>();
    if (row?.live) break;
    if (row?.rev == null) return null;
    const entry = (await bundle(env, pageOf(id), row.rev))?.bySlot.get(slotOf(id));
    if (!entry?.retired) return null;
    const next: number | null = hop < REPLACEMENT_HOPS ? await replacement(env, entry, ctx) : null;
    if (next === null) return replaced ? null : { retired: id };
    id = next; replaced = true;
  }
  return !replaced && publicId(id) === param ? { id } : { moved: id };
}

/** The URL with the crop's public id in place of `param`, in its path segment or query value. */
export function cropUrl(url: URL, param: string, id: number): URL {
  const target = new URL(url), name = publicId(id);
  target.pathname = target.pathname.split('/').map(part => {
    try { return decodeURIComponent(part) === param ? name : part; } catch { return part; }
  }).join('/');
  for (const [key, value] of [...target.searchParams]) if (value === param) target.searchParams.set(key, name);
  return target;
}

export function movedResponse(url: URL, param: string, id: number): Response {
  return new Response(null, { status: OLD_URL_STATUS, headers: { location: cropUrl(url, param, id).href,
    'cache-control': OLD_URL_STATUS === 301 ? 'public, max-age=86400' : 'no-store' } });
}

// A write names crops by public id. An old text id comes only from a tab opened before the cutover,
// whose crops may have moved since: it is refused with a prompt to reload.
export const STALE_CODE = 'reload';
export const STALE_MESSAGE = 'This page is from before an update. Reload it.';
export class StaleCrop extends Error {
  constructor() { super(STALE_MESSAGE); }
}
/** The crop id in a crop field of a write body (the crop a review, mark or favourite is about), or
 *  null for a value that is no crop id. Throws StaleCrop for a text id. Only crop fields come here: a
 *  claim's `subject` is a book, form or word key kept as text, and a claim on a crop names the crop in
 *  its own `crop` field. */
export function writtenCropField(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  if (textId(value)) throw new StaleCrop();
  return cropOf(value);
}
export const staleResponse = (headers: Record<string, string> = {}) => Response.json(
  { detail: STALE_MESSAGE, code: STALE_CODE },
  { status: 409, headers: { ...headers, 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
