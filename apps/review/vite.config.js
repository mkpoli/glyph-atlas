import { readFileSync } from 'node:fs'
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

export default defineConfig({
  plugins: [externalWasm, sveltekit()],
  define: { __ORT_VERSION__: JSON.stringify(ort.version) },
  build: { target: 'es2022', sourcemap: false },
  server: { port: 5173, strictPort: true },
})
