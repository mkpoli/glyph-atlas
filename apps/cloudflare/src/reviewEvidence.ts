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

/** Missing or damaged evidence must fail the export, so a partial page cannot be acknowledged. */
export async function readReviewEvidence(env: Env, hash: string): Promise<Record<string, Json>> {
  if (!/^[0-9a-f]{64}$/.test(hash)) throw new Problem(503, 'Invalid review evidence reference.');
  const object = await env.MEDIA.get(evidenceKey(hash));
  if (!object) throw new Problem(503, 'Review evidence is unavailable.');
  const bytes = await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  if (await digest(bytes) !== hash) throw new Problem(503, 'Review evidence failed its integrity check.');
  const saved = JSON.parse(new TextDecoder().decode(bytes));
  if (saved.version !== 1 || !saved.reviews || typeof saved.reviews !== 'object' || Array.isArray(saved.reviews))
    throw new Problem(503, 'Invalid review evidence object.');
  return saved.reviews;
}

export const legacyKey = (page: number) => `v3/review-legacy/${page}.json.gz`;
/** The reviews of a page's crops moved from the old site, by `legacy:<old event id>`; null when the page
 *  has none archived. */
export async function readLegacyReviews(env: Env, page: number): Promise<Record<string, Json> | null> {
  const object = await env.MEDIA.get(legacyKey(page));
  if (!object) return null;
  const saved = JSON.parse(new TextDecoder().decode(await new Response(object.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()));
  if (saved.version !== 1 || !saved.reviews || typeof saved.reviews !== 'object' || Array.isArray(saved.reviews))
    throw new Problem(503, 'Invalid legacy review archive.');
  return saved.reviews;
}
