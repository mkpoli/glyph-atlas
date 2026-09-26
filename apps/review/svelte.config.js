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
  },
}
