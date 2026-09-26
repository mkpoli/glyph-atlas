import adapter from '@sveltejs/adapter-cloudflare'
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'

// The Worker's configuration, bindings and routes live with the API in `apps/cloudflare`.
const wrangler = '../cloudflare/wrangler.jsonc'

export default {
  preprocess: vitePreprocess(),
  compilerOptions: {
    // Svelte 5 runes only; the app is new and has no legacy components.
    runes: true,
  },
  kit: {
    adapter: adapter({ config: wrangler, platformProxy: { configPath: wrangler } }),
    alias: {
      $components: 'src/components',
      $views: 'src/views',
    },
    // Put each page's stylesheets in its HTML. As separate files they block the first paint behind
    // extra requests that, on a slow phone connection, finish after the first glyph images arrive.
    inlineStyleThreshold: 64 * 1024,
  },
}
