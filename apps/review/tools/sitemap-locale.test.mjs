import { describe, expect, it, mock } from 'bun:test'
import { plugin } from 'bun'
import { readdirSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { compileModule } from 'svelte/compiler'

const LOCALE_DIR = join(import.meta.dir, '../src/locales')
const localeEntries = Object.fromEntries(
  readdirSync(LOCALE_DIR)
    .filter(name => name.endsWith('.json'))
    .map(name => [`../locales/${name}`, JSON.parse(readFileSync(join(LOCALE_DIR, name), 'utf8'))]),
)

plugin({
  name: 'sitemap-locale-svelte-loader',
  setup(build) {
    build.onLoad({ filter: /\.svelte\.js$/ }, async ({ path }) => {
      let source = await readFile(path, 'utf8')
      if (source.includes("import.meta.glob('../locales/*.json'")) {
        source = source.replace(
          "import.meta.glob('../locales/*.json', { eager: true, import: 'default' })",
          JSON.stringify(localeEntries),
        )
      }
      const { js } = compileModule(source, { filename: path, generate: 'server' })
      return { contents: js.code, loader: 'js' }
    })
  },
})

const i18n = await import('../src/lib/i18n.svelte.js')
mock.module('$lib/i18n.svelte.js', () => i18n)
mock.module('$lib/sitemap.js', () => ({
  pagePaths: async () => ['/'],
  characterPaths: async () => [],
  urlset: (_origin, _paths, tags) => new Response(tags.join(',')),
}))
const { GET: pagesSitemap } = await import('../src/routes/sitemaps/pages/[tag].xml/+server.js')

describe('sitemap locale validation', () => {
  it('rejects inherited Object prototype property names as language tags', async () => {
    for (const tag of ['constructor', 'toString', '__proto__', 'valueOf']) {
      expect(i18n.isLocale(tag), tag).toBe(false)
      expect(i18n.delocalize(`/${tag}/contact`), tag).toEqual({ tag: null, path: `/${tag}/contact` })
      let caught = null
      try {
        await pagesSitemap({ fetch: () => {}, params: { tag }, url: new URL(`https://atlas.test/sitemaps/pages/${tag}.xml`) })
      } catch (err) {
        caught = err
      }
      expect(caught?.status, tag).toBe(404)
    }
    expect(i18n.isLocale('ja')).toBe(true)
  })
})
