// Image search: the classifier runs in the reader's browser, so an image they choose stays on their
// device; only the vector it computes is sent to the site, which finds the crops nearest to it.
//
// The model is downloaded only when the reader asks for it, and kept in Cache Storage under the
// version the site published (`/atlas/similar/model`), with the onnxruntime-web WebAssembly that runs
// it. A newer version, or a page built with another onnxruntime-web, is offered as an update, which
// the reader downloads before searching again.
import { preprocess, tensor } from './preprocess.js'

const CACHE = 'glyph-atlas-image-search'
const FILES = '/atlas/similar/files/'
// A key of its own in the cache that records what is installed: the model's version and files.
const INSTALLED = '/atlas/similar/installed.json'
// The onnxruntime-web this build bundles; its WebAssembly is published under the same version.
export const RUNTIME_VERSION = __ORT_VERSION__
const RUNTIME = `runtime/onnxruntime-web-${RUNTIME_VERSION}/`
const RUNTIME_FILE = 'ort-wasm-simd-threaded.asyncify.wasm'
/** Classes below this probability are not offered as candidates, as in the review panel. */
const CANDIDATE_FLOOR = .015
const CANDIDATES = 5

export const supported = () => typeof window !== 'undefined' && 'caches' in window && typeof WebAssembly === 'object'

async function json(path, options) {
  const response = await fetch(path, options)
  const value = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : response.statusText)
    error.status = response.status
    Object.assign(error, { detail: value })
    throw error
  }
  return value
}

/** The model the site offers, the runtime it runs on, and whether the index answers it. */
export async function offered() {
  const info = await json('/atlas/similar/model')
  if (!info.model) return { ...info, runtime: null }
  const runtime = await json(FILES + RUNTIME + 'runtime.json').catch(() => null)
  return { ...info, runtime: runtime && { ...runtime, path: RUNTIME + RUNTIME_FILE } }
}

/** Every byte a download of `info` fetches. */
export const downloadSize = info => info?.model ? info.model.files.model.bytes + info.model.files.classes.bytes + (info.runtime?.bytes ?? 0) : 0

/** What this browser keeps, or null. */
export async function installed() {
  if (!supported()) return null
  const cache = await caches.open(CACHE)
  const record = await cache.match(INSTALLED)
  if (!record) return null
  const value = await record.json()
  // A browser may evict part of the cache; a record whose files are gone is no installation.
  for (const path of value.paths) if (!await cache.match(FILES + path)) return null
  return value
}

/** Whether the browser keeps the files through storage pressure, and how much it holds for the site. */
export async function storage() {
  const manager = navigator.storage
  return { persisted: await manager?.persisted?.().catch(() => false) ?? false, estimate: await manager?.estimate?.().catch(() => null) ?? null }
}

async function sha256(buffer) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(b => b.toString(16).padStart(2, '0')).join('')
}

/** One file, read with its progress reported, checked against its digest. */
async function fetchFile(path, bytes, digest, progress, signal) {
  const response = await fetch(FILES + path, { signal, cache: 'no-store' })
  if (!response.ok || !response.body) throw new Error(`${path}: ${response.status}`)
  const reader = response.body.getReader(), parts = []
  let received = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value); received += value.length
    progress(received)
  }
  const buffer = await new Blob(parts).arrayBuffer()
  if (buffer.byteLength !== bytes || await sha256(buffer) !== digest) throw new Error(`${path} did not arrive intact`)
  return buffer
}

/**
 * Download `info`'s model, class list and runtime into the cache, reporting the bytes received, then
 * ask the browser to keep them. A previous version is removed once the new one is in place.
 */
export async function download(info, onprogress = () => {}, signal = undefined) {
  const { model, runtime } = info
  if (!runtime) throw new Error('The runtime for this model is not published.')
  const files = [
    [model.files.model.path, model.files.model.bytes, model.files.model.sha256, 'application/octet-stream'],
    [model.files.classes.path, model.files.classes.bytes, model.files.classes.sha256, 'application/json'],
    [runtime.path, runtime.bytes, runtime.sha256, 'application/wasm'],
  ]
  const total = files.reduce((sum, [, bytes]) => sum + bytes, 0)
  let before = 0
  const fetched = []
  for (const [path, bytes, digest, type] of files) {
    const buffer = await fetchFile(path, bytes, digest, received => onprogress(before + received, total), signal)
    fetched.push([path, buffer, type])
    before += bytes
  }
  if (signal?.aborted) throw new DOMException('The download was cancelled.', 'AbortError')
  await release()
  await caches.delete(CACHE)
  const cache = await caches.open(CACHE)
  for (const [path, buffer, type] of fetched)
    await cache.put(FILES + path, new Response(buffer, { headers: { 'content-type': type, 'content-length': String(buffer.byteLength) } }))
  const record = { version: model.version, encoder: model.encoder, bytes: total, runtime: RUNTIME_VERSION,
    paths: files.map(([path]) => path), preprocessing: model.preprocessing, saved: new Date().toISOString() }
  await cache.put(INSTALLED, Response.json(record))
  await navigator.storage?.persist?.().catch(() => false)
  return record
}

/** Remove the kept model and runtime. */
export async function remove() {
  await release()
  await caches.delete(CACHE)
}

let loaded = null

/** Free the running session, whose weights are as large as the model. */
// Counts releases: a load that was under way when its model was released or replaced drops its session.
let generation = 0

async function release() {
  generation++
  const session = loaded?.session
  loaded = null
  await session?.release?.().catch(() => {})
}

async function cached(path) {
  const response = await (await caches.open(CACHE)).match(FILES + path)
  if (!response) throw new Error(`${path} is not downloaded`)
  return response
}

/** The installed model, ready to run: WebGPU where the browser offers it, else WebAssembly on one thread. */
export async function load(record) {
  if (loaded?.version === record.version) return loaded
  const started = generation
  const [modelPath, classesPath, runtimePath] = record.paths
  const [ort, model, classes, wasm] = await Promise.all([
    import('onnxruntime-web/webgpu'),
    cached(modelPath).then(r => r.arrayBuffer()),
    cached(classesPath).then(r => r.json()),
    cached(runtimePath).then(r => r.arrayBuffer()),
  ])
  // The WebAssembly comes from the cache, so nothing is fetched while the model loads. One thread
  // needs no cross-origin isolation.
  ort.env.wasm.wasmBinary = wasm
  ort.env.wasm.numThreads = 1
  ort.env.logLevel = 'error'
  let session = null, backend = 'wasm'
  if (typeof navigator !== 'undefined' && navigator.gpu) {
    try {
      session = await ort.InferenceSession.create(model, { executionProviders: ['webgpu'], graphOptimizationLevel: 'all' })
      backend = 'webgpu'
    } catch { session = null }
  }
  session ??= await ort.InferenceSession.create(model, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' })
  if (started !== generation) {
    await session.release?.().catch(() => {})
    throw new DOMException('The model was removed while it loaded.', 'AbortError')
  }
  loaded = { version: record.version, encoder: record.encoder, session, classes, backend, ort,
    size: record.preprocessing?.size ?? 128, mean: record.preprocessing?.mean ?? 0.449, std: record.preprocessing?.std ?? 0.226 }
  return loaded
}

/** The class probabilities and the unit vector of an RGBA crop, and the grey square the model saw. */
export async function embed(model, rgba, width, height) {
  const grey = preprocess(rgba, width, height, model.size)
  const input = new model.ort.Tensor('float32', tensor(grey, model.size, model.mean, model.std), [1, 3, model.size, model.size])
  const started = performance.now()
  const outputs = await model.session.run({ pixel_values: input }, ['probs', 'features'])
  const probs = outputs.probs.data, raw = outputs.features.data
  const norm = Math.hypot(...raw)
  const features = Float32Array.from(raw, value => value / norm)
  return { probs, features, grey, milliseconds: performance.now() - started }
}

/**
 * The likeliest characters, as the review panel lists them: a family CODH trained as one class is
 * one candidate with all its members. The abstention class `other` is reported apart.
 */
export function candidatesOf(classes, probs) {
  const groups = new Map()
  let other = 0
  classes.forEach((entry, index) => {
    if (!entry.chars.length) { other += probs[index]; return }
    const key = entry.family ?? entry.class
    const group = groups.get(key) ?? { key, chars: entry.chars, family: Boolean(entry.family), score: 0 }
    group.score += probs[index]
    groups.set(key, group)
  })
  const candidates = [...groups.values()].sort((a, b) => b.score - a.score)
    .filter(group => group.score >= CANDIDATE_FLOOR).slice(0, CANDIDATES)
  return { candidates, other }
}

function base64(vector) {
  const bytes = new Uint8Array(new Float32Array(vector).buffer)
  let text = ''
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(text)
}

/** The crops nearest to a vector, and each candidate's nearest crops. Only the vector leaves the device. */
export function search(encoder, features, candidates, signal = undefined) {
  return json('/atlas/similar/query', { method: 'POST', signal, headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ encoder, vector: base64(features), candidates: candidates.map(c => c.chars) }) })
}
