import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'

export default {
  preprocess: vitePreprocess(),
  compilerOptions: {
    // Svelte 5 runes only; the app is new and has no legacy components.
    runes: true,
  },
}
