import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

/** The API paths the review service owns; the dev server forwards them to it. */
const API = ['/atlas', '/lines', '/forms', '/history', '/images', '/layers', '/reviews', '/openapi.json']

const target = process.env.ATLAS_REVIEW_API ?? 'http://127.0.0.1:8770'

export default defineConfig({
  plugins: [svelte()],
  // The built interface is mounted by the review service at `/`, so assets are requested from `/`.
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(API.map((path) => [path, { target, changeOrigin: true }])),
  },
})
