import { Problem } from './busy';

type Json = Record<string, any>;
export const evidenceKey = (hash: string) => `v3/review-evidence/${hash}.json.gz`;
const digest = async (bytes: BufferSource) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
  .map(byte => byte.toString(16).padStart(2, '0')).join('');

/** Save before the D1 transaction. A losing retry leaves an unreferenced immutable object. */
export async function storeReviewEvidence(env: Env, reviews: Record<string, Json>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify({ version: 1, reviews }));
  const hash = await digest(bytes);
  const zipped = await new Response(new Response(bytes).body!.pipeThrough(new CompressionStream('gzip'))).arrayBuffer();
  await env.MEDIA.put(evidenceKey(hash), zipped, { httpMetadata: { contentType: 'application/json', contentEncoding: 'gzip' } });
  return hash;
}

/** The reviews a stored object holds: gzipped JSON `{version: 1, reviews: {...}}`. Anything else, bytes
 *  that do not unzip or parse included, is a 503 naming the object, so a caller retries or stops. */
async function stored(object: { body: ReadableStream }, what: string, hash?: string): Promise<Record<string, Json>> {
  let bytes: ArrayBuffer, saved: unknown;
  try { bytes = await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer() }
  catch { throw new Problem(503, `The ${what} could not be read.`) }
  if (hash !== undefined && await digest(bytes) !== hash) throw new Problem(503, 'Review evidence failed its integrity check.');
  try { saved = JSON.parse(new TextDecoder().decode(bytes)) }
  catch { throw new Problem(503, `The ${what} could not be read.`) }
  const reviews = saved && typeof saved === 'object' && !Array.isArray(saved) ? (saved as Json).reviews : undefined;
  if ((saved as Json | null)?.version !== 1 || !reviews || typeof reviews !== 'object' || Array.isArray(reviews))
    throw new Problem(503, `Invalid ${what}.`);
  return reviews;
}

/** Missing or damaged evidence is a 503: a save stops on it, and the export lists the reviews it held as unavailable. */
export async function readReviewEvidence(env: Env, hash: string): Promise<Record<string, Json>> {
  if (!/^[0-9a-f]{64}$/.test(hash)) throw new Problem(503, 'Invalid review evidence reference.');
  const object = await env.MEDIA.get(evidenceKey(hash));
  if (!object) throw new Problem(503, 'Review evidence is unavailable.');
  return stored(object, 'review evidence object', hash);
}

export const legacyKey = (page: number) => `v3/review-legacy/${page}.json.gz`;
/** The reviews of a page's crops moved from the old site, by `legacy:<old event id>`; null when the page
 *  has none archived. */
export async function readLegacyReviews(env: Env, page: number): Promise<Record<string, Json> | null> {
  const object = await env.MEDIA.get(legacyKey(page));
  return object ? stored(object, 'legacy review archive') : null;
}
