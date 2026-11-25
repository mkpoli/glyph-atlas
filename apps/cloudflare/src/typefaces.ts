// Atlas Sans and Atlas Serif, the two faces the site sets characters in (`scripts/build_atlas_fonts.py`,
// docs/typefaces.md): a build is stored in R2 as `fonts/<version>/`, its font files under `<face>/`
// beside the style sheets that declare them, and served under /atlas/fonts/. A version is the hash of
// the sources and the build and never changes once stored, so its files are cached for good, and a
// file it lacks stays missing: that answer is kept at the edge too, for five minutes. Only the
// versions `typefaceVersions.ts` names are served. Sealie, the small seal label font
// (`scripts/build_seal_font.py`), is a build of its own with its files under `seal/`.
import { StorageUnavailable } from './busy';
import { FONT_VERSIONS } from './typefaceVersions';

const TYPES: Record<string, string> = { woff2: 'font/woff2', css: 'text/css; charset=utf-8', json: 'application/json', txt: 'text/plain; charset=utf-8' };
const FILE = /^(?:sans|serif)\/(?:figures\/)?[0-9a-f]{3}\.woff2$|^(?:sans|serif)\/bold\/(?:[cu][0-9]|[0-9a-f]{3})\.woff2$|^(?:sans|serif)\/figures\/[0-9a-f]{3}\.json$|^(?:sans|serif)\/[cu][0-9]\.woff2$|^(?:sans|serif)(?:-figures)?\.css$|^seal\/(?:s|[0-9a-f]{3})\.woff2$|^index\.json$|^(?:NOTICE|OFL)\.txt$/;

/** A file of a served version, a 404 when the version lacks it, or null when the path names none. R2
 *  failing is StorageUnavailable. */
export async function typefaceFile(env: Env, path: string, request?: Request, ctx?: ExecutionContext,
  versions: readonly string[] = FONT_VERSIONS): Promise<Response | null> {
  const slash = path.indexOf('/');
  const version = path.slice(0, slash), file = path.slice(slash + 1);
  if (slash < 0 || !versions.includes(version) || !FILE.test(file)) return null;
  const cache = request && typeof caches !== 'undefined' ? (caches as any).default as Cache : null;
  const key = request ? new Request(new URL(new URL(request.url).pathname, request.url)) : null;
  const hit = cache && key ? await cache.match(key) : undefined;
  if (hit) return hit;
  let object: R2ObjectBody | null;
  try { object = await env.MEDIA.get(`fonts/${version}/${file}`); }
  catch (error) { throw new StorageUnavailable(`fonts/${version}/${file} could not be read`, { cause: error }); }
  const response = object ? new Response(object.body, { headers: {
    'content-type': TYPES[file.split('.').pop()!],
    'content-length': String(object.size), etag: object.httpEtag,
    'cache-control': 'public, max-age=31536000, immutable', 'x-content-type-options': 'nosniff',
  } }) : Response.json({ detail: 'No such font file.' }, { status: 404, headers: { 'cache-control': 'public, max-age=300' } });
  if (cache && key && ctx) ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}
