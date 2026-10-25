// The composer's web export (han-compose `compose_ids.py export-web`), which the review site's IDS editor
// draws its preview from: stored in R2 as `composer/<version>/<path>`, and served under
// /atlas/composer/. A version is the composer's commit and layout and never changes once stored, so
// its files are cached for good, and a file it lacks stays missing: that answer is kept at the edge
// too, for five minutes. Only the versions `composerVersions.ts` names are served. A path is any file
// of the export, in folders as deep as it nests them. Any site may read it: the export is public and
// read-only, and a read sends no credentials.
import { StorageUnavailable } from './busy';
import { COMPOSER_VERSIONS } from './composerVersions';

const FILE = /^(?:[A-Za-z0-9_-]{1,64}\/){0,4}[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.(?:json|txt)(?:\.gz)?$/;
const TYPES: Record<string, string> = { json: 'application/json', txt: 'text/plain; charset=utf-8' };
const SHARED: Record<string, string> = { 'access-control-allow-origin': '*', 'cross-origin-resource-policy': 'cross-origin' };

/** The answer to another site's preflight for a composer file. */
export const composerPreflight = () => new Response(null, { status: 204, headers: {
  ...SHARED, 'access-control-allow-methods': 'GET, HEAD, OPTIONS', 'access-control-max-age': '86400',
} });

/** A file of the export, a 404 when the version lacks it, or null when the path names no file of a
 *  served version. R2 failing is StorageUnavailable. */
export async function composerFile(env: Env, path: string, request?: Request, ctx?: ExecutionContext,
  versions: readonly string[] = COMPOSER_VERSIONS): Promise<Response | null> {
  const slash = path.indexOf('/');
  const version = path.slice(0, slash), file = path.slice(slash + 1);
  if (slash < 0 || !versions.includes(version) || !FILE.test(file) || file.includes('..')) return null;
  const cache = request && typeof caches !== 'undefined' ? (caches as any).default as Cache : null;
  const key = request ? new Request(new URL(new URL(request.url).pathname, request.url)) : null;
  const hit = cache && key ? await cache.match(key) : undefined;
  // A copy kept at the edge before the export was shared lacks the sharing headers.
  if (hit) { const shared = new Response(hit.body, hit); for (const [k, v] of Object.entries(SHARED)) shared.headers.set(k, v); return shared; }
  let object: R2ObjectBody | null;
  try { object = await env.MEDIA.get(`composer/${version}/${file}`); }
  catch (error) { throw new StorageUnavailable(`composer/${version}/${file} could not be read`, { cause: error }); }
  const response = object ? new Response(object.body, { headers: {
    // The record and host files are gzip bytes under a .json name, which the review app unpacks itself:
    // sent as bytes, so nothing on the way decodes or compresses them again.
    'content-type': /^[gp]\//.test(file) || file.endsWith('.gz') ? 'application/octet-stream' : TYPES[file.split('.').pop()!],
    'content-length': String(object.size), etag: object.httpEtag,
    'cache-control': 'public, max-age=31536000, immutable', 'x-content-type-options': 'nosniff', ...SHARED,
  } }) : Response.json({ detail: 'No such composer file.' }, { status: 404, headers: { 'cache-control': 'public, max-age=300', ...SHARED } });
  if (cache && key && ctx) ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}
