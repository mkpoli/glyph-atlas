// Drawings of forms named by an ideographic description sequence, made by the composer when the site
// is built and stored in R2 as `ids/<sha256 of the sequence's NFC form>.svg` (`drawingKey` in the
// review app). A sequence keeps its key when the composer redraws it, so a drawing is cached for a day
// rather than for good.
const FILE = /^[0-9a-f]{64}\.svg$/;
const HEADERS = { 'content-type': 'image/svg+xml', 'cache-control': 'public, max-age=86400',
  'x-content-type-options': 'nosniff', 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'" };

/** The drawing stored under `ids/<name>`, or null when the name is malformed or nothing is drawn. */
export async function drawingFile(env: Env, name: string, request?: Request, ctx?: ExecutionContext): Promise<Response | null> {
  if (!FILE.test(name)) return null;
  const cache = request && typeof caches !== 'undefined' ? (caches as any).default as Cache : null;
  const key = request ? new Request(request.url) : null;
  const hit = cache && key ? await cache.match(key) : undefined;
  if (hit) return hit;
  const object = await env.MEDIA.get(`ids/${name}`);
  if (!object) return null;
  const response = new Response(object.body, { headers: { ...HEADERS, 'content-length': String(object.size), etag: object.httpEtag } });
  if (cache && key && ctx) ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}
