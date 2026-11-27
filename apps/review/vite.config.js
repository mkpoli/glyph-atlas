import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { sveltekit } from '@sveltejs/kit/vite'
import { defineConfig } from 'vite'

// The image search fetches the onnxruntime-web WebAssembly of the version it bundles, from R2.
const ort = JSON.parse(readFileSync(new URL('./node_modules/onnxruntime-web/package.json', import.meta.url), 'utf8'))

// The bundle names its WebAssembly with `new URL(..., import.meta.url)`, which Vite would copy into the
// static assets; at 27 MB it is over what a Workers asset may be. The page hands the runtime the bytes
// it downloaded (`env.wasm.wasmBinary`), so that URL is never fetched and is left unresolved here.
const externalWasm = {
  name: 'onnxruntime-external-wasm',
  enforce: 'pre',
  transform(code, id) {
    if (!id.includes('/onnxruntime-web/dist/')) return null
    return code.replaceAll(/new URL\(("ort-wasm[\w.-]*\.wasm"),import\.meta\.url\)/g, 'new URL($1,self.location.href)')
  },
}

// han-compose's bundled outline files (Bopomofo, Tangut, Small Seal, Jurchen, Shuishu, New Korean,
// palette parts, custom forms, and Unicode fallback shards): emitted as static assets in the client
// build and served under `/composer-data/` in dev, then passed to the worker via `loaders`.
const COMPOSER_DATA = fileURLToPath(new URL('./node_modules/han-compose/data/', import.meta.url))
const COMPOSER_OUTLINES = [
  ...['tangut', 'bopomofo', 'new-korean', 'small-seal', 'shuishu', 'jurchen', 'palette'].map(name => `sources/${name}/outlines.json.gz`),
  'numbered-sans-outlines.json.gz',
  'custom-forms.json.gz',
  ...readdirSync(join(COMPOSER_DATA, 'unicode/outlines')).map(file => `unicode/outlines/${file}`),
]
const COMPOSER_SET = new Set(COMPOSER_OUTLINES)
const COMPOSER_MODULE = '\0composer-outlines'
const COMPOSER_PREFIX = '/composer-data/'

function composerOutlines() {
  let emitted = false
  return {
    name: 'composer-outlines',
    resolveId: id => id === 'composer-outlines' ? COMPOSER_MODULE : null,
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = decodeURIComponent((request.url ?? '').split('?')[0])
        if (!path.startsWith(COMPOSER_PREFIX)) return next()
        const name = path.slice(COMPOSER_PREFIX.length)
        if (!COMPOSER_SET.has(name)) return next()
        response.setHeader('content-type', 'application/octet-stream')
        response.end(readFileSync(join(COMPOSER_DATA, name)))
      })
    },
    load(id) {
      if (id !== COMPOSER_MODULE) return null
      if (!emitted && this.environment?.name === 'client' && this.meta && !this.meta.watchMode) {
        emitted = true
        for (const path of COMPOSER_OUTLINES) {
          this.emitFile({ type: 'asset', fileName: `composer-data/${path}`, source: readFileSync(join(COMPOSER_DATA, path)) })
        }
      }
      return `export default ${JSON.stringify(Object.fromEntries(COMPOSER_OUTLINES.map(path => [path, `${COMPOSER_PREFIX}${path}`])))}`
    },
  }
}

// Each kanji's Japanese readings, the inverse of `data/vocab/kanji-readings.json` (Unihan kJapanese, in
// hiragana): emitted as KANJI_SHARDS static files by code point, so the reading editor reads the one or two a run needs.
const KANJI_READINGS = fileURLToPath(new URL('../../data/vocab/kanji-readings.json', import.meta.url))
const KANJI_SHARDS = 64, KANJI_PREFIX = '/kanji-readings/', KANJI_MODULE = '\0kanji-readings'
function kanjiShards() {
  const shards = Array.from({ length: KANJI_SHARDS }, () => ({}))
  for (const [reading, kanji] of Object.entries(JSON.parse(readFileSync(KANJI_READINGS, 'utf8')).readings))
    for (const char of new Set(kanji)) (shards[char.codePointAt(0) % KANJI_SHARDS][char] ??= []).push(reading)
  for (const shard of shards) for (const char in shard) shard[char] = [...new Set(shard[char])].sort((a, b) => a.localeCompare(b, 'ja'))
  return shards.map(shard => JSON.stringify(shard))
}
function kanjiReadings() {
  let shards = null, emitted = false
  const built = () => shards ??= kanjiShards()
  return {
    name: 'kanji-readings',
    resolveId: id => id === 'kanji-readings' ? KANJI_MODULE : null,
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const found = /^\/kanji-readings\/(0|[1-9]\d*)\.json$/.exec((request.url ?? '').split('?')[0]), n = found ? Number(found[1]) : -1
        if (n < 0 || n >= KANJI_SHARDS) return next()
        response.setHeader('content-type', 'application/json')
        response.end(built()[n])
      })
    },
    load(id) {
      if (id !== KANJI_MODULE) return null
      if (!emitted && this.environment?.name === 'client' && this.meta && !this.meta.watchMode) {
        emitted = true
        built().forEach((source, n) => this.emitFile({ type: 'asset', fileName: `kanji-readings/${n}.json`, source }))
      }
      return `export const SHARDS = ${KANJI_SHARDS}, PREFIX = ${JSON.stringify(KANJI_PREFIX)}`
    },
  }
}

export default defineConfig({
  plugins: [externalWasm, composerOutlines(), kanjiReadings(), sveltekit()],
  define: { __ORT_VERSION__: JSON.stringify(ort.version) },
  build: { target: 'es2022', sourcemap: false },
  worker: { format: 'es' },
  server: { port: 5173, strictPort: true },
})
