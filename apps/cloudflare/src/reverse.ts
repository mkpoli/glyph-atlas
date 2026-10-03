// Image search: a reader's browser runs the classifier on an image they choose and sends only the
// vector it computes, never the image. The vector is searched in the Vectorize index
// `scripts/publish_reverse.sh` keeps at the current similar-crop revision.
//
// R2 holds what the search needs, under `reverse/`:
// - `model.json`: the browser model's manifest (models/classifier/browser_model.py), whose files are
//   `models/<version>/classifier.onnx` and `models/<version>/classes.json`;
// - `runtime/onnxruntime-web-<version>/`: the onnxruntime-web WebAssembly the page runs it with, and
//   `runtime.json`, its size and SHA-256;
// - `index/<index name>.json`: the encoder, revision and width of the vectors that index holds. The
//   Worker reads the pointer of the index it is bound to (`SIMILAR_INDEX_NAME`), so a new index can be
//   filled and published before a deploy binds it.
// The page offers the model for download and asks for its files here; nothing is fetched until the
// reader chooses to.
import { resolveNeighbours, type ItemsFor } from './similar';

type Json = Record<string, any>;
export type IndexPointer = { index: string; encoder: string; revision: string; crops: number; dimensions: number };

/** A search request is a vector and up to `CANDIDATES` candidate characters' sets: a few kilobytes. */
export const MAX_BODY = 16384;
export const CANDIDATES = 5;
const CHARACTERS_PER_CANDIDATE = 16;
/** A label is a character or a short sequence; a candidate string longer than this is no label. */
const CODE_POINTS_PER_CHARACTER = 8;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
/** Crops shown in the "looks like, any character" list and in each candidate's list. */
export const SIMILAR_LIMIT = 24;
export const CANDIDATE_LIMIT = 12;
const POINTER_SECONDS = 300;

let pointers: { model: Json | null; index: IndexPointer | null; read: number } | null = null;

export function resetPointers() {
  pointers = null;
}

const isModel = (value: any) => value && typeof value.version === 'string' && /^[0-9a-f]{16}$/.test(value.version)
  && typeof value.encoder === 'string' && ['model', 'classes'].every(name => typeof value.files?.[name]?.name === 'string'
    && Number.isInteger(value.files[name].bytes) && typeof value.files[name].sha256 === 'string');
const isIndex = (value: any) => value && typeof value.index === 'string' && typeof value.encoder === 'string'
  && typeof value.revision === 'string' && Number.isInteger(value.dimensions) && value.dimensions > 0;

async function current(env: Env) {
  if (pointers && Date.now() - pointers.read < POINTER_SECONDS * 1000) return pointers;
  // A pointer that is missing or malformed counts as none, and is not reread on every request.
  const read = async (key: string, valid: (value: any) => boolean) => {
    try { const object = await env.MEDIA.get(key); const value = object ? await object.json<any>() : null; return valid(value) ? value : null }
    catch { return null }
  };
  const name = env.SIMILAR_INDEX_NAME;
  const [model, index] = await Promise.all([read('reverse/model.json', isModel),
    name ? read(`reverse/index/${name}.json`, value => isIndex(value) && value.index === name) : null]);
  pointers = { model, index, read: Date.now() };
  return pointers;
}

/** What the page shows before a download: the current model, and whether the index answers its vectors. */
export async function modelInfo(env: Env): Promise<Json> {
  const { model, index } = await current(env);
  return {
    model: model && { version: model.version, encoder: model.encoder, preprocessing: model.preprocessing, features: model.features,
      files: Object.fromEntries(['model', 'classes'].map(name => [name, { path: `models/${model.version}/${model.files[name].name}`,
        bytes: model.files[name].bytes, sha256: model.files[name].sha256 }])) },
    index: index && { revision: index.revision, crops: index.crops, encoder: index.encoder },
    ready: Boolean(model && index && env.SIMILAR_INDEX && model.encoder === index.encoder && model.features === index.dimensions),
  };
}

const FILE = /^(models\/[0-9a-f]{16}\/(?:classifier\.onnx|classes\.json)|runtime\/onnxruntime-web-\d+\.\d+\.\d+(?:-[\w.]+)?\/[\w.-]+\.(?:wasm|mjs|json))$/;
const TYPES: Record<string, string> = { onnx: 'application/octet-stream', json: 'application/json', wasm: 'application/wasm', mjs: 'text/javascript' };

/**
 * A model or runtime file. Each path names one immutable version, so a browser keeps it for good and
 * the edge cache answers repeats without reading R2.
 */
export async function modelFile(env: Env, path: string, request?: Request, ctx?: ExecutionContext): Promise<Response | null> {
  if (!FILE.test(path)) return null;
  const cache = request && typeof caches !== 'undefined' ? (caches as any).default as Cache : null;
  const key = request ? new Request(request.url) : null;
  const hit = cache && key ? await cache.match(key) : undefined;
  if (hit) return request?.method === 'HEAD' ? new Response(null, { headers: hit.headers }) : hit;
  const head = request?.method === 'HEAD';
  const object = head ? await env.MEDIA.head(`reverse/${path}`) : await env.MEDIA.get(`reverse/${path}`);
  if (!object) return null;
  const headers = { 'content-type': TYPES[path.split('.').pop()!], 'content-length': String(object.size), etag: object.httpEtag,
    'cache-control': 'public, max-age=31536000, immutable', 'x-content-type-options': 'nosniff' };
  if (head) return new Response(null, { headers });
  const response = new Response((object as R2ObjectBody).body, { headers });
  if (cache && key && ctx) ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}

/** A rate-limit key for an address: an IPv6 client holds a whole /64, so it is keyed by that prefix. */
export function addressKey(address: string | null): string {
  if (!address) return 'local';
  if (!address.includes(':')) return address;
  const [head, tail = ''] = address.split('::');
  const left = head ? head.split(':') : [], right = tail ? tail.split(':') : [];
  const groups = address.includes('::') ? [...left, ...Array(8 - left.length - right.length).fill('0'), ...right] : left;
  return groups.slice(0, 4).map(group => group.toLowerCase().replace(/^0+(?=.)/, '')).join(':') + '::/64';
}

/** The body as text, or null once it passes `limit` bytes; a larger body is never held in full. */
export async function boundedText(request: Request, limit: number): Promise<string | null> {
  const declared = Number(request.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > limit) return null;
  if (!request.body) return '';
  const reader = request.body.getReader(), parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); return null }
    parts.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.byteLength }
  return new TextDecoder().decode(bytes);
}

export class QueryError extends Error {
  constructor(public status: number, message: string, public extra: Json = {}) { super(message) }
}

/** The vector of a request: base64 of little-endian float32 values, finite, of the index's width and unit length. */
export function decodeVector(value: unknown, dimensions: number): number[] {
  if (typeof value !== 'string' || value.length !== Math.ceil(dimensions * 4 / 3) * 4) throw new QueryError(400, 'The vector is not the width the index holds.');
  let bytes: Uint8Array;
  try { bytes = Uint8Array.from(atob(value), c => c.charCodeAt(0)) } catch { throw new QueryError(400, 'The vector is not base64.') }
  if (bytes.length !== dimensions * 4) throw new QueryError(400, 'The vector is not the width the index holds.');
  const view = new DataView(bytes.buffer);
  const vector = Array.from({ length: dimensions }, (_, i) => view.getFloat32(i * 4, true));
  if (!vector.every(Number.isFinite)) throw new QueryError(400, 'The vector holds a value that is not a number.');
  const norm = Math.hypot(...vector);
  if (Math.abs(norm - 1) > 1e-2) throw new QueryError(400, 'The vector is not of unit length.');
  return vector;
}

/** The candidates of a request: up to `CANDIDATES` sets of characters, each set what one class stands for. */
export function decodeCandidates(value: unknown): string[][] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > CANDIDATES) throw new QueryError(400, `Name at most ${CANDIDATES} candidates.`);
  return value.map(set => {
    if (!Array.isArray(set) || !set.length || set.length > CHARACTERS_PER_CANDIDATE
      || !set.every(c => typeof c === 'string' && c.length > 0 && [...c].length <= CODE_POINTS_PER_CHARACTER
        && !LONE_SURROGATE.test(c) && !/\p{Cc}/u.test(c)))
      throw new QueryError(400, 'A candidate is a list of characters.');
    return [...new Set(set as string[])];
  });
}

/**
 * The crops that look most like a vector, and the crops of each candidate character that look most
 * like it. A request from a model other than the one the index was built with is refused with the
 * version to update to.
 */
export async function query(env: Env, input: Json, itemsFor: ItemsFor): Promise<Json> {
  const { model, index } = await current(env);
  if (!index || !env.SIMILAR_INDEX) throw new QueryError(503, 'Image search is not available yet.');
  if (input.encoder !== index.encoder)
    throw new QueryError(409, 'This model is out of date.', { error: 'model', version: model?.encoder === index.encoder ? model.version : null });
  const vector = decodeVector(input.vector, index.dimensions);
  const candidates = decodeCandidates(input.candidates);
  // Ids and scores only: the crops' records come from D1, which has their current labels.
  const search = (topK: number, filter?: VectorizeVectorMetadataFilter) =>
    env.SIMILAR_INDEX.query(vector, { topK, returnMetadata: 'none', returnValues: false, ...(filter ? { filter } : {}) })
      .then(found => found.matches.map(m => [m.id, Math.round(m.score * 1000) / 1000] as [string, number]));
  let similar: [string, number][], perCandidate: [string, number][][];
  try {
    // A candidate's list is filtered by the label the index was filled with; a crop relabelled since
    // is dropped below, so each list asks for more than it shows.
    [similar, ...perCandidate] = await Promise.all([
      search(SIMILAR_LIMIT * 2),
      ...candidates.map(set => search(CANDIDATE_LIMIT * 3, { label: set.length === 1 ? set[0] : { $in: set } })),
    ]);
  } catch {
    throw new QueryError(503, 'Image search is not available right now. Try again later.');
  }
  const known = new Map<string, Json | null>();
  return {
    revision: index.revision,
    similar: await resolveNeighbours(env, similar, SIMILAR_LIMIT, itemsFor, known),
    candidates: await Promise.all(perCandidate.map(async (list, n) => ({
      characters: candidates[n],
      crops: await resolveNeighbours(env, list, CANDIDATE_LIMIT, itemsFor, known, item => candidates[n].includes(item.label)) }))),
  };
}
