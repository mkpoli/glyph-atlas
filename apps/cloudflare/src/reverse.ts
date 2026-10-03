// Image search: a reader's browser runs the classifier on an image they choose and sends only the
// vector it computes, never the image. The vector is searched in the Vectorize index
// `scripts/publish_reverse.sh` keeps at the current similar-crop revision.
//
// R2 holds what the search needs, under `reverse/`:
// - `model.json`: the browser model's manifest (models/classifier/browser_model.py), whose files are
//   `models/<version>/classifier.onnx` and `models/<version>/classes.json`;
// - `runtime/onnxruntime-web-<version>/`: the onnxruntime-web WebAssembly the page runs it with, and
//   `runtime.json`, its size and SHA-256;
// - `index.json`: the encoder, revision and width of the vectors the bound index holds.
// The page offers the model for download and asks for its files here; nothing is fetched until the
// reader chooses to.
import { resolveNeighbours, type ItemsFor } from './similar';

type Json = Record<string, any>;
export type IndexPointer = { index: string; encoder: string; revision: string; crops: number; dimensions: number };

/** A search request is a vector and up to `CANDIDATES` candidate characters' sets: a few kilobytes. */
export const MAX_BODY = 16384;
export const CANDIDATES = 5;
const CHARACTERS_PER_CANDIDATE = 16;
/** Crops shown in the "looks like, any character" list and in each candidate's list. */
export const SIMILAR_LIMIT = 24;
export const CANDIDATE_LIMIT = 12;
const POINTER_SECONDS = 300;

let pointers: { model: Json | null; index: IndexPointer | null; read: number } | null = null;

export function resetPointers() {
  pointers = null;
}

async function current(env: Env) {
  if (pointers && Date.now() - pointers.read < POINTER_SECONDS * 1000) return pointers;
  const read = async (key: string) => { const object = await env.MEDIA.get(key); return object ? object.json<any>() : null };
  const [model, index] = await Promise.all([read('reverse/model.json'), read('reverse/index.json')]);
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
    ready: Boolean(model && index && env.SIMILAR_INDEX && model.encoder === index.encoder),
  };
}

const FILE = /^(models\/[0-9a-f]{16}\/(?:classifier\.onnx|classes\.json)|runtime\/onnxruntime-web-\d+\.\d+\.\d+(?:-[\w.]+)?\/[\w.-]+\.(?:wasm|mjs|json))$/;
const TYPES: Record<string, string> = { onnx: 'application/octet-stream', json: 'application/json', wasm: 'application/wasm', mjs: 'text/javascript' };

/** A model or runtime file. Each path names one immutable version, so a browser keeps it for good. */
export async function modelFile(env: Env, path: string): Promise<Response | null> {
  if (!FILE.test(path)) return null;
  const object = await env.MEDIA.get(`reverse/${path}`);
  if (!object) return null;
  return new Response(object.body, { headers: {
    'content-type': TYPES[path.split('.').pop()!], 'content-length': String(object.size), etag: object.httpEtag,
    'cache-control': 'public, max-age=31536000, immutable', 'x-content-type-options': 'nosniff' } });
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
      || !set.every(c => typeof c === 'string' && c.length > 0 && c.length <= 16))
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
  const [similar, ...perCandidate] = await Promise.all([
    search(SIMILAR_LIMIT * 2),
    ...candidates.map(set => search(CANDIDATE_LIMIT * 2, { label: set.length === 1 ? set[0] : { $in: set } })),
  ]);
  const known = new Map<string, Json | null>();
  return {
    revision: index.revision,
    similar: await resolveNeighbours(env, similar, SIMILAR_LIMIT, itemsFor, known),
    candidates: await Promise.all(perCandidate.map(async (list, n) => ({
      characters: candidates[n], crops: await resolveNeighbours(env, list, CANDIDATE_LIMIT, itemsFor, known) }))),
  };
}
